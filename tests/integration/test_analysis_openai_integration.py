"""Paid API requires explicit marker AND credentials; one synthetic abstract only."""

import os

import pytest
from analysis_fakes import FakeZoteroClient

from research_workbench.analysis.corpus import ResearchCorpusBuilder
from research_workbench.application.analysis import AnalysisService
from research_workbench.integrations.ai.openai import OpenAIAnalysisProvider
from research_workbench.persistence import (
    Database,
    ProjectRepository,
    QuestionRepository,
    QuestionSourceRepository,
)

pytestmark = pytest.mark.openai_integration


def test_real_openai_single_synthetic_abstract(tmp_path):
    if os.environ.get("RUN_OPENAI_INTEGRATION") != "1":
        pytest.skip("Real OpenAI provider: NOT TESTED (explicit RUN_OPENAI_INTEGRATION=1 required)")
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("OPENAI_MODEL"):
        pytest.skip("Real OpenAI provider: NOT TESTED (key/model not configured)")
    with Database(tmp_path / "paid provider integration.db") as db:
        db.upgrade()
        project = ProjectRepository(db).create("Temporary provider test")
        q = QuestionRepository(db).create(
            project.id, "What conclusions are supported and what remains uncertain?"
        )
        QuestionSourceRepository(db).attach_reference(q.id, "FAKE0001")
        reader = FakeZoteroClient()
        from dataclasses import replace

        reader.item = replace(
            reader.item,
            abstract=(
                "A synthetic pilot study compared two memory methods on ten toy tasks. "
                "Method A solved six tasks and method B solved five. No repeated trials or "
                "significance tests were reported. Generalization remains untested."
            ),
        )
        with OpenAIAnalysisProvider() as provider:
            run = AnalysisService(db, ResearchCorpusBuilder(reader), provider).run_analysis(
                q.id, "abstracts"
            )
        assert run.status == "completed" and run.provider == "openai" and run.result_json
