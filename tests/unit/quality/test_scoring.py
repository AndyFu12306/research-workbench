"""Hand-counted mechanism tests, not independent human review evidence."""

from dataclasses import replace

import pytest
from quality_support.contracts import Opportunity, QualityError, Review
from quality_support.loading import ROOT, decode, load_assessment, load_candidate, load_case
from quality_support.loss import error_events, loss_summary
from quality_support.scoring import count, retention_verdict, review_status, semantic_metrics
from quality_support.validation import review_targets, validate_assessment


def draft(q, name):
    case = load_case(q)
    candidate = load_candidate(case, name)
    return case, candidate, load_assessment(case, candidate)


def test_hand_counted_denominators():
    rows = tuple(
        Opportunity(str(i), v, "not_applicable" if v == "na" else "")
        for i, v in enumerate(("pass", "fail", "indeterminate", "unscored", "na"))
    )
    assert count(rows) == decode((ROOT / "expected_reports/ratio_example.json").read_text("utf-8"))
    with pytest.raises(QualityError):
        count((rows[0], rows[0]))
    with pytest.raises(QualityError):
        count((Opportunity("na", "na"),))
    assert count(())["rate"] is None


def test_dual_axes_and_inherited_error():
    case, _, a = draft("Q18", "wrong")
    metrics = semantic_metrics(case, a)
    assert metrics["upstream_support"]["success"] == 4
    assert metrics["source_support"]["success"] == 2
    events = error_events(a.claims)
    map_event = next(e for e in events if e["claim"] == "A/map/1/f1/c1")
    assert map_event["kind"] == "first_distortion"
    for alias in ("A/document/f1/c1", "cross/f1/c1"):
        inherited = next(e for e in events if e["claim"] == alias)
        assert inherited["kind"] == "inherited_error"
        assert inherited["roots"] == ["A/map/1/f1/c1"]


@pytest.mark.parametrize(
    "variant,kind",
    [("new_distortion", "first_distortion"), ("unsupported_correction", "unsupported_new")],
)
def test_new_distortion_and_unsupported_correction(variant, kind):
    _, _, a = draft("Q18", variant)
    events = error_events(a.claims)
    alias = "cross/f1/c1" if variant == "new_distortion" else "A/document/f1/c1"
    assert next(e for e in events if e["claim"] == alias)["kind"] == kind


def test_collective_q13_and_first_extraction_loss():
    case, _, a = draft("Q13", "correct")
    summary = loss_summary(case, a.retentions)
    assert summary["map_collection"]["metric"]["success"] == 0
    assert summary["document_reduce"]["metric"]["success"] == 0
    assert summary["end_to_end"]["success"] == 2
    _, _, missing = draft("Q13", "missing_condition")
    loss = loss_summary(case, missing.retentions)
    assert loss["map_collection"]["metric"]["success"] == 1
    assert loss["document_reduce"]["insufficient"] == 1
    assert loss["document_reduce"]["metric"]["N"] == 1
    assert loss["end_to_end"]["success"] == 1
    assert loss["unsupported_reappearances"] == 1
    _, _, amb = draft("Q13", "ambiguous")
    assert loss_summary(case, amb.retentions)["document_reduce"]["indeterminate_input"] == 1


def test_actual_drafts_are_never_human_reviewed():
    case, _, a = draft("Q01", "correct")
    assert review_status(case, a)["status"] == "provisional"
    targets = tuple(sorted(review_targets(case, a)))
    same = (
        Review("same", "mechanism_fixture", "primary", targets, True, "fixture opinion A"),
        Review("same", "mechanism_fixture", "secondary", targets, True, "fixture opinion B"),
    )
    assert review_status(case, replace(a, reviews=same), mechanism=True)["status"] == "provisional"
    independent = (same[0], replace(same[1], reviewer="other"))
    simulated = replace(a, reviews=independent)
    assert review_status(case, simulated, mechanism=True)["status"] == "simulation_complete"
    assert review_status(case, simulated)["status"] == "provisional"
    disputed = replace(simulated, reviews=(independent[0], replace(independent[1], agrees=False)))
    assert review_status(case, disputed, mechanism=True)["disputed"] == len(targets)
    adjudicated = replace(
        disputed,
        reviews=(
            *disputed.reviews,
            Review(
                "third", "mechanism_fixture", "adjudicator", targets, True, "fixture resolution"
            ),
        ),
    )
    assert review_status(case, adjudicated, mechanism=True)["status"] == "simulation_complete"


def test_changed_existing_claim_is_distortion_even_without_direct_contradiction():
    _, _, a = draft("Q18", "wrong")
    claims = tuple(
        replace(c, upstream_support="unsupported", source_support="unsupported", link="changed")
        if c.stage == "A/document"
        else c
        for c in a.claims
    )
    events = error_events(claims)
    assert next(e for e in events if e["claim"] == "A/document/f1/c1")["kind"] == "first_distortion"


def test_review_q13_ambiguous_is_not_faithfully_retained():
    case, _, assessment = draft("Q13", "ambiguous")
    trace = [r for r in assessment.retentions if r.fact_id == "F1"]
    assert [r.outcome for r in trace] == ["partial", "preserved", "preserved"]
    assert trace[1].availability == "indeterminate"
    assert retention_verdict("F1", assessment.retentions) == "fail"
    assert semantic_metrics(case, assessment)["end_to_end_retention"]["rate"] == "1/2"
    assert loss_summary(case, assessment.retentions)["end_to_end"]["success"] == 1


@pytest.mark.parametrize("layer", ["map_collection", "document_reduce"])
@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("partial", "fail"),
        ("absent", "fail"),
        ("distorted", "fail"),
        ("indeterminate", "indeterminate"),
        ("unscored", "unscored"),
    ],
)
def test_review_earlier_outcome_cannot_be_hidden_by_preserved_cross(layer, outcome, expected):
    case, _, assessment = draft("Q13", "correct")
    changed = replace(
        assessment,
        retentions=tuple(
            replace(r, outcome=outcome) if r.fact_id == "F1" and r.layer == layer else r
            for r in assessment.retentions
        ),
    )
    assert retention_verdict("F1", changed.retentions) == expected
    metric = semantic_metrics(case, changed)["critical_fact_retention"]
    assert metric["success"] == 1
    assert metric["N"] == 2
    assert metric["I"] == (1 if expected == "indeterminate" else 0)
    assert metric["U"] == (1 if expected == "unscored" else 0)


@pytest.mark.parametrize(
    "availability,expected",
    [("insufficient", "fail"), ("indeterminate", "indeterminate"), ("unscored", "unscored")],
)
def test_review_uncertain_input_is_not_retention_success(availability, expected):
    _, _, assessment = draft("Q13", "correct")
    changed = tuple(
        replace(r, availability=availability)
        if r.fact_id == "F1" and r.layer == "document_reduce"
        else r
        for r in assessment.retentions
    )
    assert retention_verdict("F1", changed) == expected


def test_review_known_loss_takes_precedence_over_pending_judgment():
    _, _, assessment = draft("Q13", "correct")
    changed = tuple(
        replace(r, outcome="absent")
        if r.fact_id == "F1" and r.layer == "map_collection"
        else replace(r, availability="unscored")
        if r.fact_id == "F1" and r.layer == "document_reduce"
        else r
        for r in assessment.retentions
    )
    assert retention_verdict("F1", changed) == "fail"


@pytest.mark.parametrize(
    "stances",
    [
        ("opposes", "opposes"),
        ("irrelevant", "irrelevant"),
        ("opposes", "irrelevant"),
        ("irrelevant", "opposes"),
    ],
)
@pytest.mark.parametrize("entry", ["validation", "scoring"])
def test_review_q18_supported_cannot_have_only_negative_citations(stances, entry):
    case, candidate, assessment = draft("Q18", "correct")
    claim = next(c for c in assessment.claims if c.stage == "cross")
    assert claim.source_support == "supported"
    changed_claim = replace(
        claim,
        citations=tuple(
            replace(j, stance=s) for j, s in zip(claim.citations, stances, strict=True)
        ),
    )
    changed = replace(
        assessment,
        claims=tuple(
            changed_claim if c.claim_id == claim.claim_id else c for c in assessment.claims
        ),
    )
    with pytest.raises(QualityError):
        if entry == "validation":
            validate_assessment(case, candidate, changed)
        else:
            semantic_metrics(case, changed)


@pytest.mark.parametrize(
    "stances",
    [
        ("supports", "opposes"),
        ("conditional", "conditional"),
        ("conditional", "opposes"),
        ("supports", "irrelevant"),
    ],
)
def test_review_mixed_and_conditional_evidence_remains_manually_adjudicated(stances):
    case, candidate, assessment = draft("Q18", "correct")
    claim = next(c for c in assessment.claims if c.stage == "cross")
    changed_claim = replace(
        claim,
        citations=tuple(
            replace(j, stance=s) for j, s in zip(claim.citations, stances, strict=True)
        ),
    )
    changed = replace(
        assessment,
        claims=tuple(
            changed_claim if c.claim_id == claim.claim_id else c for c in assessment.claims
        ),
    )
    validate_assessment(case, candidate, changed)
    assert semantic_metrics(case, changed)["source_support"]["success"] == len(changed.claims)
