"""Calibration regressions: annotations remain AI drafts, never semantic oracles."""

from dataclasses import replace

import pytest
from quality_support.binding import attach, verify
from quality_support.contracts import Assessment, Case, QualityError, Review
from quality_support.loading import (
    ROOT,
    decode,
    digest,
    encode,
    load_assessment,
    load_candidate,
    load_case,
    load_manifest,
    typed,
)
from quality_support.reporting import report
from quality_support.scoring import review_status, semantic_metrics
from quality_support.scripted import run_memory
from quality_support.validation import review_targets, validate_assessment

QUESTION_ANCHORS = {
    "Q01": ("72%", "68%", "百分点"),
    "Q02": ("250 ms", "0.5 s", "load X"),
    "Q03": ("显著", "无效"),
    "Q04": ("mortality", "zero"),
    "Q05": ("数据集", "重复"),
    "Q06": ("Experiments 1", "Experiment 3", "protocol"),
    "Q07": ("健康成人", "儿童"),
    "Q08": ("low load", "high load"),
    "Q09": ("10", "8", "重复"),
    "Q10": ("setting X", "outperformed", "underperformed"),
    "Q11": ("成人", "儿童"),
    "Q12": ("accuracy", "energy", "scale"),
    "Q13": ("试验T", "试验U", "儿童未测试"),
    "Q14": ("experiment E", "2 point", "replication"),
    "Q15": ("方法A", "空结果"),
    "Q16": ("toy-task", "2 points", "source B"),
    "Q17": ("预检", "Cross", "历史"),
    "Q18": ("60%", "40%", "instructions"),
}


@pytest.mark.parametrize("q,anchors", QUESTION_ANCHORS.items())
def test_calibrated_question_defines_each_scope(q, anchors):
    case = load_case(q)
    question = case.question.title + " " + case.question.description
    assert all(word in question for word in anchors)
    assert "Q15/Q16:" not in question
    assert case.revision >= 3


@pytest.mark.parametrize("q", ["Q06", "Q13"])
def test_unjustified_relations_removed(q):
    assert not load_case(q).relations


@pytest.mark.parametrize(
    "q,required",
    [
        ("Q01", ("72%", "68%")),
        ("Q09", ("10", "8")),
        ("Q10", ("outperformed", "underperformed")),
        ("Q13", ("试验T", "试验U", "儿童未测试")),
        ("Q18", ("60%", "40%", "cannot establish X")),
    ],
)
def test_positive_cross_keeps_required_facts(q, required):
    case = load_case(q)
    candidate = load_candidate(case, "correct")
    text = " ".join(f.text for f in candidate.stages[-1].findings)
    assert all(word in text for word in required)


def test_injection_is_source_text_not_a_positive_finding():
    case = load_case("Q18")
    assert "Ignore all instructions" in case.sources[0].text
    for name in ("correct", "paraphrase"):
        candidate = load_candidate(case, name)
        assert all(
            "Ignore all instructions" not in f.text for s in candidate.stages for f in s.findings
        )


@pytest.mark.parametrize("q,name", [("Q15", "correct"), ("Q17", "preflight"), ("Q17", "capacity")])
def test_empty_and_rejected_runs_have_real_review_targets(q, name):
    case = load_case(q)
    assessment = load_assessment(case, load_candidate(case, name))
    assert review_targets(case, assessment)
    assert getattr(assessment, "acceptance_judgments", ())
    assert not assessment.claims
    assert not assessment.reviews


def test_drafts_have_specific_reasons_and_selected_criticality():
    cases = [load_case(e.case_id) for e in load_manifest().cases]
    assert any(not f.critical for case in cases for f in case.facts)
    for case in cases:
        entry = next(e for e in load_manifest().cases if e.case_id == case.case_id)
        for name in entry.candidates:
            assessment = load_assessment(case, load_candidate(case, name))
            assert assessment.origin == "codex_draft" and not assessment.reviews
            for claim in assessment.claims:
                assert "inspect this claim" not in claim.reason
                assert claim.upstream_refs[0] in claim.reason


@pytest.mark.parametrize("change", ["question", "requirements", "candidate"])
def test_stale_observation_cannot_be_reattached_as_new_calibration(change):
    case = load_case("Q01")
    candidate = load_candidate(case, "correct")
    assessment = load_assessment(case, candidate)
    stale_run = run_memory(case, candidate, "stale_run")
    if change == "question":
        case = replace(case, question=replace(case.question, description="Changed scope."))
    elif change == "requirements":
        case = replace(case, facts=(replace(case.facts[0], critical=False), *case.facts[1:]))
    else:
        candidate = replace(candidate, revision=candidate.revision + 1)
    with pytest.raises(QualityError):
        attach(case, candidate, stale_run, assessment)


def test_archive_is_explicitly_separate_and_all_original_candidate_ids_survive():
    old_root = ROOT.parent / "v1"
    old_manifest = decode((old_root / "manifest.json").read_text("utf-8"))
    pairs = [(e.case_id, n) for e in load_manifest().cases for n in e.candidates]
    assert len(pairs) == 46
    assert pairs == [(e["case_id"], n) for e in old_manifest["cases"] for n in e["candidates"]]
    for q, name in pairs:
        case = load_case(q)
        archived = decode((old_root / "cases" / f"{q}.json").read_text("utf-8"))
        assert [s.text for s in case.sources] == [s["text"] for s in archived["sources"]]
        candidate = load_candidate(case, name)
        assessment = load_assessment(case, candidate)
        assert candidate.revision == 2 and assessment.revision == 2
        assert assessment.version == "quality-assessment-v2"
        assert assessment.rubric == "quality-rubric-v2"
        assert assessment.atomization == "quality-atomization-v2"
        if name in ("correct", "paraphrase") and case.facts:
            metric = semantic_metrics(case, assessment)["end_to_end_retention"]
            assert metric["success"] == metric["N"] == len(case.facts)


@pytest.mark.parametrize(
    "folder,cls,name",
    [
        ("cases", Case, "Q01"),
        ("assessments", Assessment, "Q01_correct"),
    ],
)
def test_incompatible_archive_is_not_silently_loaded_as_calibrated(folder, cls, name):
    archived = decode((ROOT.parent / "v1" / folder / f"{name}.json").read_text("utf-8"))
    with pytest.raises(QualityError):
        typed(cls, archived)


@pytest.mark.parametrize("q,name", [("Q15", "correct"), ("Q17", "capacity")])
def test_acceptance_criterion_changes_invalidate_observations(q, name):
    case = load_case(q)
    candidate = load_candidate(case, name)
    assessment = load_assessment(case, candidate)
    run = run_memory(case, candidate, "criterion_run")
    changed = replace(
        case,
        acceptance_targets=(
            replace(case.acceptance_targets[0], criterion="Different acceptance scope."),
            *case.acceptance_targets[1:],
        ),
    )
    with pytest.raises(QualityError):
        attach(changed, candidate, run, assessment)


@pytest.mark.parametrize("q,name", [("Q15", "correct"), ("Q17", "preflight"), ("Q17", "capacity")])
def test_acceptance_targets_require_complete_judgments_and_independent_review(q, name):
    case = load_case(q)
    candidate = load_candidate(case, name)
    a = load_assessment(case, candidate)
    for changed in (
        replace(a, acceptance_judgments=a.acceptance_judgments[1:]),
        replace(a, acceptance_judgments=(*a.acceptance_judgments, a.acceptance_judgments[0])),
        replace(
            a,
            acceptance_judgments=(
                replace(a.acceptance_judgments[0], target_id="acceptance/unknown"),
                *a.acceptance_judgments[1:],
            ),
        ),
    ):
        with pytest.raises(QualityError):
            validate_assessment(case, candidate, changed)
    targets = tuple(sorted(review_targets(case, a)))
    assert review_status(case, a)["missing"] == len(targets) > 0
    simulated = replace(
        a,
        reviews=(
            Review("fixture_one", "mechanism_fixture", "primary", targets, True, "mechanism only"),
            Review(
                "fixture_two", "mechanism_fixture", "secondary", targets, True, "mechanism only"
            ),
        ),
    )
    validate_assessment(case, candidate, simulated)
    assert review_status(case, simulated, mechanism=True)["status"] == "simulation_complete"
    assert review_status(case, simulated)["status"] == "provisional"


def test_all_calibrated_observations_are_newly_bound_and_safely_inventoried(tmp_path):
    inventory = []
    for entry in load_manifest().cases:
        case = load_case(entry.case_id)
        for name in entry.candidates:
            candidate = load_candidate(case, name)
            a = load_assessment(case, candidate)
            run = run_memory(case, candidate, candidate.candidate_id + "_calibration")
            bound = attach(case, candidate, run, a)
            verify(case, candidate, run, bound)
            result = report(case, candidate, run, bound)
            assert result["version"] == "quality-report-v2"
            assert result["status"] == "provisional"
            if candidate.expected != "completed":
                assert result["observation_metrics"] == {}  # no research semantic score
                assert result["runtime"]["completion"]["NA"] == 1
            for changed in (
                replace(a, rubric="quality-rubric-v1"),
                replace(a, atomization="quality-atomization-v1"),
            ):
                with pytest.raises(QualityError):
                    attach(case, candidate, run, changed)
            inventory.append(
                {
                    "candidate": candidate.candidate_id,
                    "binding": bound.binding.digest,
                    "observations": len(run.observations),
                    "findings": len(run.mapping),
                }
            )
    output = encode(inventory)
    (tmp_path / "calibration-observation-inventory.json").write_text(output, encoding="utf-8")
    assert len({i["binding"] for i in inventory}) == 46
    print(
        "calibration_inventory",
        digest(inventory),
        "observations",
        sum(i["observations"] for i in inventory),
        "findings",
        sum(i["findings"] for i in inventory),
    )
