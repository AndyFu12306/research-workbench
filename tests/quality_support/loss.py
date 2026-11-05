"""Annotator-declared propagation and availability; no textual inference."""

from .contracts import Opportunity, require
from .scoring import count, layer_metrics, retention_verdict


def error_events(claims):
    events, roots = [], {}
    seen = {}
    for c in claims:
        require(c.claim_id not in seen and all(p in seen for p in c.predecessor_claims))
        inherited = sorted({root for p in c.predecessor_claims for root in roots.get(p, ())})
        uncertain = c.upstream_support in ("indeterminate", "unscored") or c.source_support in (
            "indeterminate",
            "unscored",
        )
        kind = None
        if uncertain:
            kind = "unresolved"
        elif c.upstream_support == "supported" and c.source_support != "supported":
            kind = "inherited_error" if inherited and c.link == "faithful" else "unresolved"
        elif c.upstream_support != "supported":
            kind = (
                "first_distortion"
                if (
                    c.link == "changed" or c.link == "root" and c.upstream_support == "contradicted"
                )
                and c.source_support != "supported"
                else "unsupported_new"
            )
        if kind:
            current_roots = (
                inherited
                if kind == "inherited_error"
                else [c.claim_id]
                if kind in ("first_distortion", "unsupported_new")
                else []
            )
            roots[c.claim_id] = current_roots
            events.append(
                {
                    "claim": c.claim_id,
                    "stage": c.stage,
                    "kind": kind,
                    "roots": current_roots,
                    "inherited_roots": inherited,
                    "severity": c.severity,
                }
            )
        seen[c.claim_id] = c
    return events


def loss_summary(case, retentions):
    result = layer_metrics(retentions)
    result["end_to_end"] = count(
        tuple(
            Opportunity(
                f.fact_id,
                retention_verdict(f.fact_id, retentions),
            )
            for f in case.facts
        )
    )
    result["unsupported_reappearances"] = sum(
        r.availability == "insufficient" and r.outcome == "preserved" for r in retentions
    )
    return result
