from http.client import IncompleteRead
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from research_workbench.integrations.zotero import (
    ZoteroClient,
    ZoteroCliNotFoundError,
    ZoteroParseError,
    ZoteroUnavailableError,
)
from research_workbench.integrations.zotero import client as module

KEY = "FAKE0001"


def item_data():
    return {
        "key": KEY,
        "title": "研究方法",
        "itemType": "journalArticle",
        "date": "2026",
        "abstractNote": "A non-empty abstract.",
        "creators": [
            {"firstName": "Ada", "lastName": "Researcher", "creatorType": "author"},
            {"name": "Research Group", "creatorType": "author"},
            {"name": "Editor", "creatorType": "editor"},
        ],
    }


@pytest.fixture
def api():
    obj = ZoteroClient()
    obj._runner = Mock()
    return obj


def test_search_uses_argument_list_and_keeps_query_positional(api):
    api._runner.run_json.return_value = {
        "count": 1,
        "items": [{"key": KEY, "title": "研究方法", "raw": item_data()}],
    }
    query = '--limit=999; $(do-not-run) "quoted" 研究'
    result = api.search_items(query, limit=4)
    api._runner.run_json.assert_called_once_with(
        ["search", "--limit", "4", "--detail", "full", "--", query]
    )
    assert result[0].authors == ("Ada Researcher", "Research Group")
    assert result[0].abstract == "A non-empty abstract."
    assert result[0].publication_date == "2026"


def test_empty_search_is_valid(api):
    api._runner.run_json.return_value = {"count": 0, "items": []}
    assert api.search_items("nonexistent") == ()


@pytest.mark.parametrize(
    "data", [{"items": []}, {"count": 2, "items": []}, {"count": 1, "items": [{"key": KEY}]}]
)
def test_malformed_search_is_not_an_empty_success(api, data):
    api._runner.run_json.return_value = data
    with pytest.raises(ZoteroParseError):
        api.search_items("query")


def test_metadata_normalizes_raw_response(api):
    api._runner.run_json.return_value = {"key": KEY, "data": item_data()}
    item = api.get_item(KEY)
    assert item.item_key == KEY
    assert item.title == "研究方法"
    assert item.item_type == "journalArticle"
    api._runner.run_json.assert_called_once_with(["get", "metadata", KEY])


@pytest.mark.parametrize(
    "field,value",
    [("key", "OTHER001"), ("title", None), ("creators", "invalid"), ("deleted", True)],
)
def test_metadata_rejects_mismatch_and_bad_fields(api, field, value):
    data = item_data()
    data[field] = value
    api._runner.run_json.return_value = {"key": KEY, "data": data}
    with pytest.raises(ZoteroParseError):
        api.get_item(KEY)


def test_missing_optional_metadata_remains_missing(api):
    data = {"key": KEY, "title": "Title", "itemType": "book"}
    api._runner.run_json.return_value = {"key": KEY, "data": data}
    result = api.get_item(KEY)
    assert result.abstract is None and result.publication_date is None and result.authors == ()


@pytest.mark.parametrize("key", ["bad", "--help", "lower001", "FAKE0001;delete"])
def test_invalid_item_key_never_reaches_cli(api, key):
    with pytest.raises(ValueError):
        api.get_item(key)
    api._runner.run_json.assert_not_called()


@pytest.mark.parametrize("limit", [0, 101, True, "10"])
def test_invalid_search_limit(api, limit):
    with pytest.raises(ValueError):
        api.search_items("query", limit=limit)


def test_complete_fulltext_needs_one_command(api):
    api._runner.run_json.return_value = {
        "item_key": KEY,
        "text": "Full text",
        "chars": 9,
        "truncated": False,
    }
    assert api.get_fulltext(KEY).text == "Full text"
    api._runner.run_json.assert_called_once_with(["get", "fulltext", KEY])


def test_truncated_fulltext_reads_every_page(api):
    text = "## Page 1\nFirst page\n## Page 2\nSecond page"
    api._runner.run_json.side_effect = [
        {"item_key": KEY, "text": "partial", "chars": 7, "truncated": True, "page_count": 2},
        {"item_key": KEY, "text": text, "chars": len(text), "start_page": 1, "end_page": 2},
    ]
    result = api.get_fulltext(KEY)
    assert result.page_count == 2 and result.text == text
    assert api._runner.run_json.call_args.args[0] == [
        "read",
        KEY,
        "--start-page",
        "1",
        "--end-page",
        "2",
        "--format",
        "text",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "## Page 1\nOnly one",
        "## Page 1\n\n## Page 2\n",
        "## Page 2\nWrong order\n## Page 1\nFirst",
    ],
)
def test_incomplete_page_read_is_not_fulltext_success(api, text):
    api._runner.run_json.side_effect = [
        {"item_key": KEY, "text": "partial", "chars": 7, "truncated": True, "page_count": 2},
        {"item_key": KEY, "text": text, "chars": len(text), "start_page": 1, "end_page": 2},
    ]
    with pytest.raises(ZoteroParseError, match="every page"):
        api.get_fulltext(KEY)


def test_legitimate_blank_page_does_not_invalidate_complete_coverage(api):
    text = "## Page 1\n\n## Page 2\nExtracted text"
    api._runner.run_json.side_effect = [
        {"item_key": KEY, "text": "partial", "chars": 7, "truncated": True, "page_count": 2},
        {"item_key": KEY, "text": text, "chars": len(text), "start_page": 1, "end_page": 2},
    ]
    assert api.get_fulltext(KEY).page_count == 2


def test_fulltext_missing_truncation_status_fails(api):
    api._runner.run_json.return_value = {"item_key": KEY, "text": "possibly partial"}
    with pytest.raises(ZoteroParseError):
        api.get_fulltext(KEY)


def test_annotations_empty_and_nonempty_contract(api):
    api._runner.run_json.return_value = []
    assert api.get_annotations(KEY) == ()
    api._runner.run_json.return_value = [
        {
            "annotation_key": "ANNO0001",
            "item_key": KEY,
            "attachment_key": "ATTA0001",
            "type": "highlight",
            "page": "v",
            "page_index": 4,
            "text": "Evidence",
            "comment": "User comment",
        }
    ]
    result = api.get_annotations(KEY)[0]
    assert result.page_index == 4 and result.page == "v" and result.text == "Evidence"


def test_invalid_annotation_position_is_parse_error(api):
    api._runner.run_json.return_value = [{"page_index": "4"}]
    with pytest.raises(ZoteroParseError):
        api.get_annotations(KEY)


def test_health_is_healthy_only_when_api_and_cli_pass(api, monkeypatch):
    monkeypatch.setattr(api, "_check_local_api", Mock())
    api._runner.run_json.return_value = {
        "settings": {"ZOTERO_LOCAL": "true", "ZOTERO_BACKEND": "api"}
    }
    health = api.health_check()
    assert health.status == "healthy"
    assert health.client.status == health.local_api.status == health.cli.status == "healthy"


def test_health_preserves_unknown_client_when_api_fails(api, monkeypatch):
    monkeypatch.setattr(
        api, "_check_local_api", Mock(side_effect=ZoteroUnavailableError("refused"))
    )
    api._runner.run_json.return_value = {
        "settings": {"ZOTERO_LOCAL": "true", "ZOTERO_BACKEND": "api"}
    }
    health = api.health_check()
    assert health.status == "degraded" and health.client.status == "degraded"
    assert health.local_api.status == "unavailable"
    api._runner.run_json.side_effect = ZoteroCliNotFoundError("missing")
    assert api.health_check().status == "unavailable"


def test_health_does_not_hide_missing_cli(api, monkeypatch):
    monkeypatch.setattr(api, "_check_local_api", Mock())
    api._runner.run_json.side_effect = ZoteroCliNotFoundError("missing")
    assert api.health_check().status == "degraded"


def test_api_probe_uses_local_get_without_proxy(monkeypatch):
    response = Mock()
    response.status = 200
    response.read.return_value = b'[{"key":"FAKE0001","data":{}}]'
    opener = Mock()
    opener.open.return_value.__enter__ = Mock(return_value=response)
    opener.open.return_value.__exit__ = Mock(return_value=False)
    build = Mock(return_value=opener)
    monkeypatch.setattr(module, "build_opener", build)
    ZoteroClient(api_timeout=2)._check_local_api()
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://localhost:23119/api/users/0/items?limit=1"
    assert request.get_method() == "GET"
    assert opener.open.call_args.kwargs == {"timeout": 2}
    assert build.call_args.args[0].proxies == {}
    assert (
        build.call_args.args[1].redirect_request(None, None, 302, "", {}, "https://example.com")
        is None
    )


def test_api_connection_error_maps_to_unavailable(monkeypatch):
    opener = Mock()
    opener.open.side_effect = URLError("connection refused")
    monkeypatch.setattr(module, "build_opener", Mock(return_value=opener))
    with pytest.raises(ZoteroUnavailableError, match="connection refused"):
        ZoteroClient()._check_local_api()


def test_api_premature_http_response_is_unavailable(monkeypatch):
    opener = Mock()
    opener.open.side_effect = IncompleteRead(b"partial")
    monkeypatch.setattr(module, "build_opener", Mock(return_value=opener))
    with pytest.raises(ZoteroUnavailableError):
        ZoteroClient()._check_local_api()
