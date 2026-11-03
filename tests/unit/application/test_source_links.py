from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from research_workbench.application import HydrationStatus, QuestionSourceService
from research_workbench.integrations.zotero import (
    ZoteroClient,
    ZoteroCliNotFoundError,
    ZoteroCommandError,
    ZoteroItem,
    ZoteroItemNotFoundError,
    ZoteroParseError,
    ZoteroTimeoutError,
    ZoteroUnavailableError,
)
from research_workbench.persistence import EntityNotFoundError
from research_workbench.persistence.source_models import QuestionSourceRow, SourceRow

KEY = "FAKE0001"


@pytest.fixture
def reader():
    client = Mock(spec=ZoteroClient)
    client.get_item.return_value = ZoteroItem(
        KEY, "LIVE_METADATA_SENTINEL", ("An Author",), "Live abstract", "journalArticle", "2026"
    )
    return client


@pytest.fixture
def service(database, reader):
    return QuestionSourceService(database, reader)


@pytest.fixture
def question(projects, questions):
    return questions.create(projects.create("Research project").id, "Research question")


def counts(database):
    with database._session() as session:
        return (
            session.scalar(select(func.count()).select_from(SourceRow)),
            session.scalar(select(func.count()).select_from(QuestionSourceRow)),
        )


def test_valid_attach_is_scoped_and_does_not_persist_metadata(database, service, reader, question):
    link = service.attach_zotero_source(question.id, KEY)
    assert link.status is HydrationStatus.RESOLVED
    assert link.source.external_key == KEY and link.source.scope == "personal"
    assert link.source.provider == "zotero"
    assert link.metadata == reader.get_item.return_value
    assert counts(database) == (1, 1)
    assert b"LIVE_METADATA_SENTINEL" not in database.path.read_bytes()
    reader.get_fulltext.assert_not_called()


def test_missing_question_is_checked_before_external_call(service, reader):
    with pytest.raises(EntityNotFoundError):
        service.attach_zotero_source(9999, KEY)
    reader.get_item.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        ZoteroItemNotFoundError("missing"),
        ZoteroUnavailableError("offline"),
        ZoteroCliNotFoundError("CLI missing"),
        ZoteroTimeoutError("timeout"),
        ZoteroParseError("invalid JSON"),
        ZoteroCommandError("dependency failed"),
        ValueError("bad key"),
    ],
)
def test_failed_validation_leaves_db_unchanged(database, service, reader, question, error):
    reader.get_item.side_effect = error
    with pytest.raises(type(error)):
        service.attach_zotero_source(question.id, KEY)
    assert counts(database) == (0, 0)


def test_duplicate_attach_preserves_identity_and_link_timestamp(
    database, service, reader, question
):
    first = service.attach_zotero_source(question.id, KEY)
    assert service.attach_zotero_source(question.id, KEY) == first
    assert counts(database) == (1, 1)
    assert reader.get_item.call_count == 2


def test_hydration_retrieves_current_metadata_each_time(service, reader, question):
    first = service.attach_zotero_source(question.id, KEY)
    newer = replace(reader.get_item.return_value, title="Changed in Zotero")
    reader.get_item.return_value = newer
    hydrated = service.list_question_sources(question.id)
    assert hydrated[0].source == first.source
    assert hydrated[0].metadata.title == "Changed in Zotero"
    newest = replace(newer, title="Changed again")
    reader.get_item.return_value = newest
    assert service.list_question_sources(question.id)[0].metadata.title == "Changed again"


@pytest.mark.parametrize(
    "error,status,code",
    [
        (ZoteroItemNotFoundError("missing"), HydrationStatus.UNRESOLVED, "item_not_found"),
        (
            ZoteroItemNotFoundError("trash", deleted=True),
            HydrationStatus.UNRESOLVED,
            "item_deleted",
        ),
        (ZoteroUnavailableError("offline"), HydrationStatus.UNAVAILABLE, "metadata_unavailable"),
        (
            ZoteroCliNotFoundError("not installed"),
            HydrationStatus.UNAVAILABLE,
            "metadata_unavailable",
        ),
        (ZoteroTimeoutError("timeout"), HydrationStatus.UNAVAILABLE, "metadata_unavailable"),
        (ZoteroCommandError("failed"), HydrationStatus.UNAVAILABLE, "metadata_unavailable"),
        (ZoteroParseError("bad JSON"), HydrationStatus.ERROR, "invalid_metadata"),
    ],
)
def test_hydration_failure_preserves_reference_and_link(
    database, service, reader, question, error, status, code
):
    existing = service.attach_zotero_source(question.id, KEY)
    reader.get_item.side_effect = error
    result = service.list_question_sources(question.id)[0]
    assert result.source == existing.source and result.metadata is None
    assert result.status is status and result.error_code == code
    assert counts(database) == (1, 1)
    assert service.list_question_sources(question.id, hydrate=False) == (existing.association,)


def test_detach_works_offline_and_keeps_source(database, service, reader, question, sources):
    existing = service.attach_zotero_source(question.id, KEY)
    reader.get_item.reset_mock()
    reader.get_item.side_effect = ZoteroUnavailableError("offline")
    assert service.detach_source(question.id, existing.source.id)
    assert not service.detach_source(question.id, existing.source.id)
    reader.get_item.assert_not_called()
    assert sources.get(existing.source.id) == existing.source
    assert counts(database) == (1, 0)


@pytest.mark.parametrize("item_type", ["attachment", "note", "annotation"])
def test_bibliographic_parent_is_required(database, service, reader, question, item_type):
    reader.get_item.return_value = replace(reader.get_item.return_value, item_type=item_type)
    with pytest.raises(ValueError, match="bibliographic parent"):
        service.attach_zotero_source(question.id, KEY)
    assert counts(database) == (0, 0)


def test_metadata_identity_must_match_before_persisting(database, service, reader, question):
    reader.get_item.return_value = replace(reader.get_item.return_value, item_key="FAKE0002")
    with pytest.raises(ZoteroParseError):
        service.attach_zotero_source(question.id, KEY)
    assert counts(database) == (0, 0)


def test_unsupported_scope_is_not_queried_against_personal_library(
    service, reader, question, links
):
    links.attach_reference(question.id, KEY, scope="group:42")
    item = service.list_question_sources(question.id)[0]
    assert item.status is HydrationStatus.UNRESOLVED and item.error_code == "unsupported_scope"
    reader.get_item.assert_not_called()


def test_individual_failure_does_not_hide_other_linked_sources(service, reader, question, links):
    links.attach_reference(question.id, KEY)
    links.attach_reference(question.id, "FAKE0002")
    reader.get_item.side_effect = [
        ZoteroItemNotFoundError("missing"),
        replace(reader.get_item.return_value, item_key="FAKE0002"),
    ]
    results = service.list_question_sources(question.id)
    assert [r.status for r in results] == [HydrationStatus.UNRESOLVED, HydrationStatus.RESOLVED]


def test_unexpected_programming_error_is_not_silenced(service, reader, question, links):
    links.attach_reference(question.id, KEY)
    reader.get_item.side_effect = RuntimeError("programming error")
    with pytest.raises(RuntimeError, match="programming error"):
        service.list_question_sources(question.id)
