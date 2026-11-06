"""All registered cases exercise real services/migrations using synthetic inputs only."""

import pytest
from quality_support.binding import attach
from quality_support.loading import ROOT, decode, load_assessment, load_candidate, load_case
from quality_support.reporting import report
from quality_support.scripted import (
    ObservingProvider,
    SyntheticReader,
    check_child_history,
    check_history,
)

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.stage_contracts import StageBudget
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.persistence.cross_document_repository import CrossDocumentRepository
from research_workbench.persistence.document_repository import DocumentAnalysisRepository

ENTRIES = decode((ROOT / "manifest.json").read_text("utf-8"))["cases"]


@pytest.mark.parametrize(
    "q,name",
    [(e["case_id"], e["candidates"][0]) for e in ENTRIES]
    + [
        ("Q17", "capacity"),
        ("Q18", "wrong"),
        ("Q18", "new_distortion"),
        ("Q18", "unsupported_correction"),
        ("Q13", "missing_value"),
        ("Q13", "missing_condition"),
        ("Q13", "ambiguous"),
        ("Q13", "document_loss"),
    ],
)
def test_real_fake_service_and_bound_report(database, projects, questions, links, q, name):
    case = load_case(q)
    candidate = load_candidate(case, name)
    question = questions.create(
        projects.create("Synthetic evaluation").id,
        case.question.title,
        description=case.question.description,
    )
    ids = tuple(links.attach_reference(question.id, s.identity[2]).source.id for s in case.sources)
    provider, reader = ObservingProvider(case, candidate), SyntheticReader(case)
    service = CrossDocumentAnalysisService(
        database,
        reader,
        provider,
        chunking=ChunkingConfig(case.max_chars, case.overlap_chars),
        stage_budget=StageBudget(max_input_tokens=candidate.stage_input_limit),
        cross_budget=CrossBudget(max_calls=candidate.max_calls),
    )
    repo, docs = CrossDocumentRepository(database), DocumentAnalysisRepository(database)
    if candidate.expected == "completed":
        run = service.run_analysis(question.id, ids)
        assert run.status == "completed"
        check_history(provider, repo, docs, run.id)
        assert reader.reads == [s.identity[2] for s in case.sources]
    else:
        with pytest.raises(CrossDocumentError) as exc:
            service.run_analysis(question.id, ids)
        assert exc.value.code == "budget_exceeded"
        if candidate.expected == "preflight_rejected":
            assert not provider.observations and not repo.list_by_question(question.id)
        else:
            parent = repo.list_by_question(question.id)[0]
            assert parent.status == "failed"
            assert all(
                docs.get(m.document_execution_id).status == "completed"
                for m in repo.get_members(parent.id)
            )
            assert all(o.stage != "cross" for o in provider.observations)
            assert tuple(o.stage for o in provider.observations) == (
                "A/map/1",
                "A/document",
                "B/map/1",
                "B/document",
            )
            check_child_history(provider, repo, docs, parent.id)
    observed = provider.snapshot(f"{q}_{name}_run", candidate.expected)
    bound = attach(case, candidate, observed, load_assessment(case, candidate))
    result = report(case, candidate, observed, bound)
    assert result["status"] == "provisional"
    assert result["model_quality"] == "not_tested"
    assert result["runtime"]["expected_outcome"]["success"] == 1
