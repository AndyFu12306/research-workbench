"""Hand-counted mechanism tests, not independent human review evidence."""

from dataclasses import replace

import pytest
from quality_support.contracts import Claim, Opportunity, QualityError, Review
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
    # X has one faulty Map and two inherited observations; Y and its boundary
    # are independently retained at Map, Document and Cross (six observations).
    assert metrics["upstream_support"]["success"] == 8
    assert metrics["source_support"]["success"] == 6
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
    assert summary["end_to_end"]["success"] == 3
    _, _, missing = draft("Q13", "missing_condition")
    loss = loss_summary(case, missing.retentions)
    assert loss["map_collection"]["metric"]["success"] == 2
    assert loss["document_reduce"]["insufficient"] == 2
    assert loss["document_reduce"]["metric"]["N"] == 1
    assert loss["end_to_end"]["success"] == 1
    assert loss["unsupported_reappearances"] == 2
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
    assert semantic_metrics(case, assessment)["end_to_end_retention"]["rate"] == "1/3"
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
def test_review_supported_cannot_have_only_negative_citations(stances, entry):
    # Use a real two-premise inference. Q18 X now correctly cites only source A.
    case, candidate, assessment = draft("Q01", "correct")
    claim = next(c for c in assessment.claims if c.finding == "cross/f3")
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
    case, candidate, assessment = draft("Q01", "correct")
    claim = next(c for c in assessment.claims if c.finding == "cross/f3")
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


def claim(claim_id, *, upstream="supported", source="supported", link="root", predecessors=()):
    """Minimal explicit annotations; only root propagation is exercised here."""
    base = claim_id.rsplit("/c", 1)[0]
    return Claim(
        claim_id=claim_id,
        stage=base,
        finding=base,
        start=0,
        end=1,
        upstream_support=upstream,
        source_support=source,
        upstream_refs=(),
        source_evidence=(),
        reason="synthetic claim for error-root propagation",
        citations=(),
        severity="none",
        kind_correct="pass",
        predecessor_claims=tuple(predecessors),
        link=link,
        critical=False,
    )


def first_error(claim_id="A/map/1/f1/c1"):
    return claim(claim_id, upstream="contradicted", source="contradicted", link="root")


def event_for(events, claim_id):
    return next(e for e in events if e["claim"] == claim_id)


def test_supported_faithful_claim_keeps_known_error_roots():
    root = first_error()
    faithful = claim(
        "A/document/f1/c1",
        upstream="supported",
        source="contradicted",
        link="faithful",
        predecessors=(root.claim_id,),
    )
    events = error_events((root, faithful))
    assert event_for(events, root.claim_id)["kind"] == "first_distortion"
    inherited = event_for(events, faithful.claim_id)
    assert inherited["kind"] == "inherited_error"
    assert inherited["roots"] == [root.claim_id]


@pytest.mark.parametrize("source", ["contradicted", "unsupported", "partial"])
def test_supported_combined_claim_inherits_confirmed_error_roots(source):
    root = first_error()
    combined = claim(
        "A/document/f1/c1",
        upstream="supported",
        source=source,
        link="combined",
        predecessors=(root.claim_id,),
    )
    events = error_events((root, combined))
    inherited = event_for(events, combined.claim_id)
    assert inherited["kind"] == "inherited_error"
    assert inherited["roots"] == [root.claim_id]
    assert inherited["inherited_roots"] == [root.claim_id]


def test_combined_claim_merges_and_deduplicates_predecessor_roots():
    root_a = first_error("A/map/1/f1/c1")
    root_b = first_error("B/map/1/f1/c1")
    left = claim(
        "A/document/f1/c1",
        upstream="supported",
        source="contradicted",
        link="faithful",
        predecessors=(root_a.claim_id,),
    )
    same_root = claim(
        "A/document/f2/c1",
        upstream="supported",
        source="contradicted",
        link="faithful",
        predecessors=(root_a.claim_id,),
    )
    right = claim(
        "B/document/f1/c1",
        upstream="supported",
        source="contradicted",
        link="faithful",
        predecessors=(root_b.claim_id,),
    )
    combined = claim(
        "cross/f1/c1",
        upstream="supported",
        source="contradicted",
        link="combined",
        predecessors=(left.claim_id, same_root.claim_id, right.claim_id),
    )
    events = error_events((root_a, root_b, left, same_root, right, combined))
    inherited = event_for(events, combined.claim_id)
    assert inherited["kind"] == "inherited_error"
    assert inherited["roots"] == sorted([root_a.claim_id, root_b.claim_id])
    assert len(inherited["roots"]) == 2


def test_error_roots_continue_through_combined_claim_to_descendants():
    root = first_error()
    combined = claim(
        "A/document/f1/c1",
        upstream="supported",
        source="contradicted",
        link="combined",
        predecessors=(root.claim_id,),
    )
    descendant = claim(
        "cross/f1/c1",
        upstream="supported",
        source="contradicted",
        link="faithful",
        predecessors=(combined.claim_id,),
    )
    events = error_events((root, combined, descendant))
    assert event_for(events, combined.claim_id)["roots"] == [root.claim_id]
    downstream = event_for(events, descendant.claim_id)
    assert downstream["kind"] == "inherited_error"
    assert downstream["roots"] == [root.claim_id]


def test_combined_without_confirmed_predecessor_error_does_not_invent_roots():
    left = claim("A/document/f1/c1")
    right = claim("B/document/f1/c1")
    combined = claim(
        "cross/f1/c1",
        upstream="supported",
        source="contradicted",
        link="combined",
        predecessors=(left.claim_id, right.claim_id),
    )
    events = error_events((left, right, combined))
    unresolved = event_for(events, combined.claim_id)
    assert unresolved["kind"] == "unresolved"
    assert unresolved["roots"] == []


@pytest.mark.parametrize(
    "upstream,source",
    [("indeterminate", "contradicted"), ("supported", "unscored")],
)
def test_uncertain_support_is_not_reclassified_by_inherited_roots(upstream, source):
    root = first_error()
    combined = claim(
        "A/document/f1/c1",
        upstream=upstream,
        source=source,
        link="combined",
        predecessors=(root.claim_id,),
    )
    events = error_events((root, combined))
    unresolved = event_for(events, combined.claim_id)
    assert unresolved["kind"] == "unresolved"
    assert unresolved["roots"] == []


def test_non_combined_error_classification_unchanged():
    changed = claim(
        "A/document/f1/c1", upstream="unsupported", source="unsupported", link="changed"
    )
    new_claim = claim("cross/f1/c1", upstream="unsupported", source="contradicted", link="new")
    events = error_events((changed, new_claim))
    assert event_for(events, changed.claim_id)["kind"] == "first_distortion"
    assert event_for(events, new_claim.claim_id)["kind"] == "unsupported_new"
