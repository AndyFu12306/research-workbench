"""Explicit opt-in only. These tests never create or edit Zotero data."""

import os

import pytest

from research_workbench.integrations.zotero import ZoteroClient

pytestmark = pytest.mark.integration


def test_local_health():
    health = ZoteroClient().health_check()
    assert health.status == "healthy", health


def test_readonly_workflow():
    key = os.environ.get("ZOTERO_TEST_ITEM_KEY")
    if not key:
        pytest.skip("Set ZOTERO_TEST_ITEM_KEY to an existing paper with a local PDF and abstract")
    client = ZoteroClient(timeout=60)
    results = client.search_items(os.environ.get("ZOTERO_TEST_QUERY", "agent"), limit=10)
    assert results, "Choose a query that matches the configured test paper"
    assert any(item.item_key == key for item in results), (
        "Test paper must occur in the search result"
    )
    item = client.get_item(key)
    assert item.item_key == key and item.title and item.authors
    assert item.abstract and item.abstract.strip()
    fulltext = client.get_fulltext(key)
    assert len(fulltext.text) > len(item.abstract)
    assert fulltext.item_key == key
    assert isinstance(client.get_annotations(key), tuple)


def test_nonempty_annotations_when_sample_is_configured():
    key = os.environ.get("ZOTERO_TEST_ANNOTATED_ITEM_KEY")
    if not key:
        pytest.skip("NOT TESTED WITH NON-EMPTY DATA: no annotated sample configured")
    annotations = ZoteroClient().get_annotations(key)
    assert annotations, "Configured sample must already have annotations; do not create test data"
    assert any(annotation.text or annotation.comment for annotation in annotations)
