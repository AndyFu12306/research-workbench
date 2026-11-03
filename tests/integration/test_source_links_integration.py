"""Explicit metadata-only integration: temporary Workbench DB, no Zotero writes/PDF reads."""

import os

import pytest
from sqlalchemy import inspect

from research_workbench.application import HydrationStatus, QuestionSourceService
from research_workbench.integrations.zotero import ZoteroClient
from research_workbench.persistence import (
    Database,
    ProjectRepository,
    QuestionRepository,
    SourceRepository,
)

pytestmark = pytest.mark.integration


def test_real_question_source_attach_hydrate_detach(tmp_path):
    zotero = ZoteroClient()
    health = zotero.health_check()
    assert health.status == "healthy", health
    item_key = os.environ.get("ZOTERO_TEST_ITEM_KEY")
    if not item_key:
        matches = zotero.search_items(os.environ.get("ZOTERO_TEST_QUERY", "agent"), limit=10)
        assert matches, "Configure ZOTERO_TEST_QUERY or ZOTERO_TEST_ITEM_KEY for an existing paper"
        item_key = next(
            item.item_key
            for item in matches
            if item.item_type not in {"attachment", "note", "annotation"}
        )
    with Database(tmp_path / "source integration.db") as database:
        database.upgrade()
        project = ProjectRepository(database).create("Temporary integration project")
        question = QuestionRepository(database).create(project.id, "Temporary research question")
        service = QuestionSourceService(database, zotero)
        attached = service.attach_zotero_source(question.id, item_key)
        assert attached.status is HydrationStatus.RESOLVED and attached.metadata.title
        repeated = service.attach_zotero_source(question.id, item_key)
        assert repeated.association == attached.association
        hydrated = service.list_question_sources(question.id)
        assert len(hydrated) == 1 and hydrated[0].metadata.item_key == item_key
        assert hydrated[0].status is HydrationStatus.RESOLVED
        assert {c["name"] for c in inspect(database._engine).get_columns("sources")} == {
            "id",
            "provider",
            "scope",
            "external_key",
            "created_at",
        }
        assert service.detach_source(question.id, attached.source.id)
        assert SourceRepository(database).get(attached.source.id) == attached.source
        assert zotero.get_item(item_key).item_key == item_key
