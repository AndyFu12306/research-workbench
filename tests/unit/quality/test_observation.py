"""P1 binding regressions use actual validator outputs, not script-derived IDs."""

from dataclasses import replace

import pytest
from quality_support.binding import attach, make_binding, verify
from quality_support.contracts import QualityError
from quality_support.loading import load_assessment, load_candidate, load_case
from quality_support.scripted import run_memory


def observed(q="Q01", name="correct", run_alias="run_one"):
    case = load_case(q)
    candidate = load_candidate(case, name)
    run = run_memory(case, candidate, run_alias)
    return case, candidate, run


def test_actual_observation_binding_and_claim_locations():
    case, candidate, run = observed()
    assessment = load_assessment(case, candidate)
    bound = attach(case, candidate, run, assessment)
    verify(case, candidate, run, bound)
    assert len(bound.claim_locations) == len(assessment.claims)
    assert all(len(location[1]) == 64 for location in bound.claim_locations)
    assert len(run.observations) == 5


@pytest.mark.parametrize(
    "change",
    [
        "case",
        "source",
        "question",
        "requirements",
        "candidate",
        "revision",
        "run",
        "raw",
        "input",
        "projection",
        "finding_mapping",
        "config",
        "rubric",
        "terminal",
    ],
)
def test_old_scoring_rejected_after_each_change(change):
    case, candidate, run = observed()
    bound = attach(case, candidate, run, load_assessment(case, candidate))
    if change == "case":
        case = replace(case, revision=case.revision + 1)
    if change == "source":
        case = replace(case, sources=(replace(case.sources[0], text="different"), case.sources[1]))
    if change == "question":
        case = replace(case, question=replace(case.question, description="different"))
    if change == "requirements":
        case = replace(case, requirements_version="v2")
    if change == "candidate":
        candidate = replace(candidate, candidate_id="other")
    if change == "revision":
        candidate = replace(candidate, revision=candidate.revision + 1)
    if change == "run":
        run = replace(run, run_alias="another_run")
    if change in ("raw", "input", "projection"):
        field = {"raw": "raw_sha256", "input": "input_sha256", "projection": "projection_json"}[
            change
        ]
        run = replace(
            run,
            observations=(
                replace(run.observations[0], **{field: "changed"}),
                *run.observations[1:],
            ),
        )
    if change == "finding_mapping":
        run = replace(run, mapping=(*run.mapping[:-1], ("cross", "cross/f1", "0" * 64)))
    if change == "config":
        run = replace(run, config_json="{}")
    if change == "rubric":
        bound = replace(bound, assessment=replace(bound.assessment, rubric="v2"))
    if change == "terminal":
        run = replace(run, terminal="interrupted")
    with pytest.raises(QualityError):
        verify(case, candidate, run, bound)


def test_operational_metadata_does_not_change_semantic_binding():
    case, candidate, run = observed()
    before = make_binding(case, candidate, run)
    assert (
        make_binding(
            case,
            candidate,
            replace(run, metadata_json='{"seconds":999,"db_id":999,"path":"temporary"}'),
        )
        == before
    )


def test_observer_uses_returned_prose_and_other_finding_changes_ids():
    case, candidate, run = observed()

    def transform(stage, data):
        if stage == "A/map/1":
            data["findings"].append(
                {**data["findings"][0], "text": "Another synthetic returned finding."}
            )
        return data

    changed = run_memory(case, candidate, "run_one", transform=transform)
    assert run.mapping[0][2] != changed.mapping[0][2]
    assert run.observations[0].raw_sha256 != changed.observations[0].raw_sha256
    old = attach(case, candidate, run, load_assessment(case, candidate))
    with pytest.raises(QualityError):
        verify(case, candidate, changed, old)


def test_cross_stage_borrowing_is_rejected():
    case, candidate, run = observed()
    bound = attach(case, candidate, run, load_assessment(case, candidate))
    bad = replace(bound, claim_locations=(bound.claim_locations[-1], *bound.claim_locations[1:]))
    with pytest.raises(QualityError):
        verify(case, candidate, run, bad)


def test_actual_budget_change_is_observed_even_with_identical_prose(monkeypatch):
    from quality_support.scripted import ObservingProvider

    case, candidate, run = observed()
    bound = attach(case, candidate, run, load_assessment(case, candidate))
    original = ObservingProvider.generate_stage

    def changed_budget(self, request):
        return original(
            self, replace(request, budget=replace(request.budget, safety_margin_tokens=1025))
        )

    monkeypatch.setattr(ObservingProvider, "generate_stage", changed_budget)
    changed = run_memory(case, candidate, "run_one")
    assert changed.mapping == run.mapping
    assert changed.observations[0].input_sha256 != run.observations[0].input_sha256
    with pytest.raises(QualityError):
        verify(case, candidate, changed, bound)
