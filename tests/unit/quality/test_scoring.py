"""Hand-counted mechanism tests, not independent human review evidence."""

from dataclasses import replace

import pytest
from quality_support.contracts import Opportunity, QualityError, Review
from quality_support.loading import ROOT, decode, load_assessment, load_candidate, load_case
from quality_support.loss import error_events, loss_summary
from quality_support.scoring import count, review_status, semantic_metrics
from quality_support.validation import review_targets


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
