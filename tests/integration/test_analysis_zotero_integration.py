"""Explicit local Zotero abstracts → fake provider → temporary analysis DB."""

import os

import pytest
from analysis_fakes import FakeAnalysisProvider

from research_workbench.analysis.corpus import ResearchCorpusBuilder
from research_workbench.application.analysis import AnalysisService
from research_workbench.application.source_links import QuestionSourceService
from research_workbench.integrations.zotero import ZoteroClient
from research_workbench.persistence import Database, ProjectRepository, QuestionRepository
from research_workbench.persistence.analysis_repository import AnalysisRepository

pytestmark = pytest.mark.integration


def test_real_zotero_abstract_to_persisted_analysis(tmp_path):
    zotero = ZoteroClient()
    assert zotero.health_check().status == "healthy", "Local Zotero/CLI must be available"
    key = os.environ.get("ZOTERO_TEST_ITEM_KEY")
    if not key:
        items = zotero.search_items(os.environ.get("ZOTERO_TEST_QUERY", "agent"), limit=10)
        keys = [
            i.item_key for i in items if i.item_type not in {"attachment", "note", "annotation"}
        ]
        assert keys, "Configure an existing Zotero source"
        key = next((key for key in keys if (zotero.get_item(key).abstract or "").strip()), None)
        assert key, "Configure an existing Zotero source with non-empty abstract"
    with Database(tmp_path / "analysis integration.db") as db:
        db.upgrade()
        project = ProjectRepository(db).create("Temporary test project")
        q = QuestionRepository(db).create(project.id, "What is supported by this source?")
        link = QuestionSourceService(db, zotero).attach_zotero_source(q.id, key)
        provider = FakeAnalysisProvider()
        run = AnalysisService(db, ResearchCorpusBuilder(zotero), provider).run_analysis(
            q.id, "abstracts"
        )
        assert run.status == "completed" and provider.calls == 1
        manifest = AnalysisRepository(db).get_sources(run.id)
        assert len(manifest) == 1 and manifest[0].source_id == link.source.id
        assert manifest[0].input_kind == "abstract" and manifest[0].input_chars > 0
