"""Pure arithmetic over explicit annotations. Never inspect prose for truth."""

from .contracts import Opportunity, require
from .validation import LAYERS, review_targets, unique, validate_source_support


def count(rows):
    unique([r.key for r in rows])
    require(all(r.verdict in ("pass", "fail", "indeterminate", "unscored", "na") for r in rows))
    require(all(r.verdict != "na" or bool(r.reason) for r in rows))
    counts = {
        v: sum(r.verdict == v for r in rows)
        for v in ("pass", "fail", "indeterminate", "unscored", "na")
    }
    j, i, u = counts["pass"] + counts["fail"], counts["indeterminate"], counts["unscored"]
    n = j + i + u
    return {
        "success": counts["pass"],
        "N": n,
        "J": j,
        "I": i,
        "U": u,
        "NA": counts["na"],
        "rate": f"{counts['pass']}/{j}" if j else None,
        "coverage": f"{j}/{n}" if n else None,
        "status": "provisional" if i or u else "complete" if n else "not_applicable",
    }


def verdict(value, success):
    if value in ("indeterminate", "unscored"):
        return value
    return "pass" if value == success else "fail"


def retention_verdict(fact_id, retentions):
    trace = [r for r in retentions if r.fact_id == fact_id]
    final = next((r for r in trace if r.layer == "cross_document_reduce"), None)
    if final is None:
        return "unscored"
    # Faithful retention requires the whole trace, not just a correct final value.
    # Known loss takes precedence; pending judgments remain I/U, never success.
    if any(
        r.outcome in ("partial", "absent", "distorted") or r.availability == "insufficient"
        for r in trace
    ):
        return "fail"
    if any(r.outcome == "unscored" or r.availability == "unscored" for r in trace):
        return "unscored"
    if any(r.outcome == "indeterminate" or r.availability == "indeterminate" for r in trace):
        return "indeterminate"
    return verdict(final.outcome, "preserved")


def semantic_metrics(case, assessment):
    """Counts stage observations, not independent answers; report per-stage as well."""
    for claim in assessment.claims:
        validate_source_support(claim)
    metrics = {}
    for axis in ("upstream_support", "source_support"):
        metrics[axis] = count(
            tuple(
                Opportunity(c.claim_id, verdict(getattr(c, axis), "supported"))
                for c in assessment.claims
            )
        )
    metrics["kind_correct"] = count(
        tuple(Opportunity(c.claim_id, c.kind_correct, "not_applicable") for c in assessment.claims)
    )
    for name, facts in (
        ("critical_fact_retention", [f for f in case.facts if f.critical]),
        ("qualification_retention", [f for f in case.facts if f.limitation]),
        ("end_to_end_retention", list(case.facts)),
    ):
        metrics[name] = count(
            tuple(
                Opportunity(
                    f.fact_id,
                    retention_verdict(f.fact_id, assessment.retentions),
                )
                for f in facts
            )
        )
    metrics["relation_classification"] = count(
        tuple(
            Opportunity(r.relation_id, r.classification, "not_applicable")
            for r in assessment.relations
        )
    )
    metrics["conflict_recall"] = count(
        tuple(
            Opportunity(r.relation_id, r.conflict_recalled, "not_a_required_conflict")
            for r in assessment.relations
        )
    )
    metrics["conflict_precision"] = count(
        tuple(
            Opportunity(r.relation_id, r.conflict_correct, "no_conflict_assertion")
            for r in assessment.relations
        )
    )
    metrics["severe_candidate_incidence"] = count(
        (
            Opportunity(
                assessment.candidate_id,
                "pass"
                if any(c.severity == "severe" for c in assessment.claims)
                else "unscored"
                if any(c.severity == "unscored" for c in assessment.claims)
                else "indeterminate"
                if any(c.severity == "indeterminate" for c in assessment.claims)
                else "fail",
            ),
        )
    )
    return metrics


def review_status(case, assessment, *, mechanism=False):
    """Self-reported human attestations are not authenticated identities.

    Mechanism fixtures can exercise transitions, but can never finalize a real report.
    """
    targets = review_targets(case, assessment)
    origin = "mechanism_fixture" if mechanism else "human"
    reviews = [r for r in assessment.reviews if r.origin == origin]
    if not mechanism and assessment.origin != "human":
        reviews = []
    missing, disputed = [], []
    for target in sorted(targets):
        primary = [r for r in reviews if r.role == "primary" and target in r.targets]
        secondary = [r for r in reviews if r.role == "secondary" and target in r.targets]
        independent = [(a, b) for a in primary for b in secondary if a.reviewer != b.reviewer]
        if not independent:
            missing.append(target)
            continue
        people = {r.reviewer for r in primary + secondary}
        disagreement = any(not r.agrees for r in primary + secondary)
        arbitrators = [
            r
            for r in reviews
            if r.role == "adjudicator"
            and target in r.targets
            and r.reviewer not in people
            and r.agrees
        ]
        if disagreement and not arbitrators:
            disputed.append(target)
    status = (
        "provisional" if missing or disputed else "simulation_complete" if mechanism else "reviewed"
    )
    # Explicit acceptance targets cover relevance/rejection behavior separately
    # from research semantics. A genuinely targetless case has no review to finish.
    if not targets:
        status = "not_applicable"
    return {
        "status": status,
        "required": len(targets),
        "missing": len(missing),
        "disputed": len(disputed),
    }


def loss_opportunities(retentions, layer):
    rows, excluded = (
        [],
        {
            "insufficient": 0,
            "indeterminate_input": 0,
            "unscored_input": 0,
            "indeterminate_output": 0,
            "unscored_output": 0,
        },
    )
    for r in retentions:
        if r.layer != layer:
            continue
        if r.availability in ("insufficient", "indeterminate", "unscored"):
            key = {
                "insufficient": "insufficient",
                "indeterminate": "indeterminate_input",
                "unscored": "unscored_input",
            }[r.availability]
            excluded[key] += 1
        elif r.outcome in ("indeterminate", "unscored"):
            excluded[f"{r.outcome}_output"] += 1
        else:
            rows.append(Opportunity(r.fact_id, "fail" if r.outcome == "preserved" else "pass"))
    return rows, excluded


def layer_metrics(retentions):
    return {
        layer: {
            "metric": count(tuple(rows)),
            "applicable": len([r for r in retentions if r.layer == layer]),
            **excluded,
        }
        for layer in LAYERS
        for rows, excluded in [loss_opportunities(retentions, layer)]
    }
