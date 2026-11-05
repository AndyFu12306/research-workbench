"""Reporting, denominators and independent-review safeguards."""

from dataclasses import replace

import pytest
from quality_support.binding import attach
from quality_support.contracts import Opportunity, QualityError
from quality_support.loading import ROOT, decode, encode, load_assessment, load_candidate, load_case
from quality_support.reporting import markdown, report, runtime_metrics, structure_metrics
from quality_support.scoring import count, semantic_metrics
from quality_support.scripted import run_memory
from quality_support.validation import validate_assessment

MANIFEST = decode((ROOT / "manifest.json").read_text("utf-8"))
PAIRS = [(e["case_id"], name) for e in MANIFEST["cases"] for name in e["candidates"]]


@pytest.mark.parametrize("q,name", PAIRS)
def test_every_candidate_observed_scored_and_safely_reported(q, name):
    case = load_case(q)
    candidate = load_candidate(case, name)
    assessment = load_assessment(case, candidate)
    run = run_memory(case, candidate, f"{q}_{name}_run")
    assert run.terminal == candidate.expected
    bound = attach(case, candidate, run, assessment)
    result = report(case, candidate, run, bound)
    assert result["status"] == "provisional"
    assert result["annotation_origin"] == "codex_draft"
    assert result["model_quality"] == "not_tested"
    assert "composite_score" not in result
    output = encode(result) + markdown(result)
    for source in case.sources:
        assert source.text not in output
    assert case.question.description not in output
    assert "AI-authored draft: inspect" not in output
    assert "reason" not in result
    rerun = replace(run, metadata_json='{"time":99,"path":"private","db_id":42}')
    assert report(case, candidate, rerun, bound) == result


def test_unscored_and_partial_never_become_supported():
    case = load_case("Q01")
    candidate = load_candidate(case, "correct")
    assessment = load_assessment(case, candidate)
    first = assessment.claims[0]
    mixed = replace(
        assessment,
        claims=(
            replace(
                first, upstream_support="unscored", source_support="partial", severity="unscored"
            ),
        ),
    )
    metrics = semantic_metrics(case, mixed)
    assert metrics["upstream_support"]["U"] == 1
    assert metrics["source_support"]["success"] == 0
    assert metrics["source_support"]["J"] == 1
    assert metrics["severe_candidate_incidence"]["U"] == 1


def test_missing_fact_or_atomic_confirmation_cannot_be_excluded():
    case = load_case("Q01")
    candidate = load_candidate(case, "correct")
    assessment = load_assessment(case, candidate)
    for changed in (
        replace(assessment, retentions=assessment.retentions[1:]),
        replace(assessment, atomized_findings=assessment.atomized_findings[1:]),
        replace(assessment, claims=assessment.claims[1:]),
    ):
        with pytest.raises(QualityError):
            validate_assessment(case, candidate, changed)


def test_mixed_sources_are_not_majority_votes():
    case = load_case("Q10")
    correct = load_assessment(case, load_candidate(case, "correct"))
    wrong = load_assessment(case, load_candidate(case, "wrong"))
    assert semantic_metrics(case, correct)["conflict_recall"]["success"] == 1
    assert semantic_metrics(case, wrong)["conflict_recall"]["success"] == 0
    q11 = load_case("Q11")
    false_conflict = load_assessment(q11, load_candidate(q11, "wrong"))
    assert semantic_metrics(q11, false_conflict)["conflict_precision"]["rate"] == "0/1"


def test_empty_support_is_na_not_perfect_and_unexpected_failure_counts():
    case = load_case("Q15")
    candidate = load_candidate(case, "correct")
    assessment = load_assessment(case, candidate)
    assert semantic_metrics(case, assessment)["source_support"]["rate"] is None
    run = run_memory(case, candidate, "empty_run")
    failed = replace(run, terminal="interrupted")
    assert runtime_metrics(candidate, failed)["completion"]["rate"] == "0/1"
    assert runtime_metrics(candidate, failed)["expected_outcome"]["success"] == 0


def test_validator_results_feed_separate_structure_metrics():
    from quality_support.scripted import ObservingProvider

    from research_workbench.analysis.stage_validation import validate_stage_output

    case = load_case("Q01")
    candidate = load_candidate(case, "correct")
    requests = []
    original = ObservingProvider.generate_stage

    # Obtain real requests without inventing IDs.
    def capture(self, request):
        requests.append(request)
        return original(self, request)

    from unittest.mock import patch

    with patch.object(ObservingProvider, "generate_stage", capture):
        run_memory(case, candidate, "structure_run")
    request = requests[0]
    raws = [
        ('{"status":"no_relevant_findings","findings":[]}', True),
        ('{"status":"findings","findings":[]}', False),
        ('{"status":"no_relevant_findings","findings":[],"extra":1}', False),
        (
            '{"status":"findings","findings":[{"text":"Synthetic","kind":"source_summary","category":"finding","citations":["unknown"]}]}',
            False,
        ),
    ]
    trials = []
    for i, (raw, valid) in enumerate(raws):
        try:
            validate_stage_output(request, raw)
            accepted = True
        except ValueError:
            accepted = False
        trials.append((f"trial{i}", valid, accepted))
    result = structure_metrics(trials)
    assert result["acceptance"]["rate"] == "1/1"
    assert result["rejection"]["rate"] == "3/3"


def test_markdown_rejects_metric_payload_and_duplicate_na():
    case = load_case("Q01")
    candidate = load_candidate(case, "correct")
    run = run_memory(case, candidate, "safe_run")
    bound = attach(case, candidate, run, load_assessment(case, candidate))
    result = report(case, candidate, run, bound)
    result["observation_metrics"]["source_support"]["raw_response"] = "PRIVATE"
    with pytest.raises(QualityError):
        markdown(result)
    with pytest.raises(QualityError):
        count((Opportunity("a", "na"),))


def test_no_real_clients_environment_or_default_database(monkeypatch):
    from research_workbench.integrations.ai.stages import DeepSeekStageProvider, OpenAIStageProvider
    from research_workbench.integrations.zotero import ZoteroClient

    def prohibited(*args, **kwargs):
        raise AssertionError("Real client/environment access forbidden")

    monkeypatch.setattr(ZoteroClient, "__init__", prohibited)
    monkeypatch.setattr(OpenAIStageProvider, "__init__", prohibited)
    monkeypatch.setattr(DeepSeekStageProvider, "__init__", prohibited)
    import os

    monkeypatch.setattr(os, "getenv", prohibited)
    case = load_case("Q18")
    candidate = load_candidate(case, "wrong")
    run = run_memory(case, candidate, "isolated_run")
    report(case, candidate, run, attach(case, candidate, run, load_assessment(case, candidate)))
