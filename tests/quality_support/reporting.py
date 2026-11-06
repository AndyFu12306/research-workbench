"""Safe projections only. Draft estimates never become human/model acceptance."""

from dataclasses import replace

from .binding import verify
from .contracts import REPORT, Opportunity, require
from .loading import digest, encode
from .loss import error_events, loss_summary
from .scoring import count, review_status, semantic_metrics


def runtime_metrics(candidate, run):
    actual = len(run.observations)
    boundary_ok = (
        run.terminal == "preflight_rejected"
        and actual == 0
        or run.terminal == "cross_rejected"
        and actual > 0
        and all(o.stage != "cross" for o in run.observations)
        or run.terminal == "completed"
        and actual > 0
        and run.observations[-1].stage == "cross"
    )
    return {
        "expected_outcome": count(
            (
                Opportunity(
                    "run", "pass" if run.terminal == candidate.expected and boundary_ok else "fail"
                ),
            )
        ),
        "completion": count((Opportunity("run", "na", "expected_rejection"),))
        if candidate.expected != "completed"
        else count((Opportunity("run", "pass" if run.terminal == "completed" else "fail"),)),
        "calls": actual,
    }


def structure_metrics(trials):
    """Each trial is (unique alias, expected valid, actually accepted by validator)."""
    return {
        "acceptance": count(
            tuple(
                Opportunity(alias, "pass" if accepted else "fail")
                for alias, valid, accepted in trials
                if valid
            )
        ),
        "rejection": count(
            tuple(
                Opportunity(alias, "fail" if accepted else "pass")
                for alias, valid, accepted in trials
                if not valid
            )
        ),
    }


def report(case, candidate, run, bound):
    verify(case, candidate, run, bound)
    assessment = bound.assessment
    review = review_status(case, assessment)
    metrics = semantic_metrics(case, assessment) if run.terminal == "completed" else {}
    events = error_events(assessment.claims)
    unresolved = (
        any(m["status"] == "provisional" for m in metrics.values())
        or any(j.verdict in ("indeterminate", "unscored") for j in assessment.acceptance_judgments)
        or any(
            r.availability in ("indeterminate", "unscored")
            or r.outcome in ("indeterminate", "unscored")
            for r in assessment.retentions
        )
        or any(e["kind"] == "unresolved" for e in events)
    )
    # Even a fully reviewed set may contain negative cases: reviewed is not quality pass.
    status = (
        "provisional" if review["status"] != "reviewed" or unresolved else "reviewed_measurement"
    )
    stage_metrics = {}
    for stage in sorted({c.stage for c in assessment.claims}):
        subset = replace(assessment, claims=tuple(c for c in assessment.claims if c.stage == stage))
        values = semantic_metrics(case, subset)
        stage_metrics[stage] = {
            k: values[k] for k in ("upstream_support", "source_support", "kind_correct")
        }
    return {
        "version": REPORT,
        "mode": "synthetic_reference",
        "case_id": case.case_id,
        "candidate_id": candidate.candidate_id,
        "binding_sha256": bound.binding.digest,
        "assessment_sha256": digest(assessment),
        "status": status,
        "review": review,
        "acceptance": count(
            tuple(Opportunity(j.target_id, j.verdict) for j in assessment.acceptance_judgments)
        ),
        "model_quality": "not_tested",
        "semantic_origin": "annotation_driven",
        "annotation_origin": assessment.origin,
        "structure": structure_metrics(tuple((o.stage, True, True) for o in run.observations)),
        "observation_metrics": metrics,
        "by_stage": stage_metrics,
        "loss": loss_summary(case, assessment.retentions) if run.terminal == "completed" else {},
        "events": events,
        "ordinary_issue_observations": sum(c.severity == "ordinary" for c in assessment.claims),
        "citation_judgments": [
            {"claim": c.claim_id, "source": j.source, "stance": j.stance}
            for c in assessment.claims
            for j in c.citations
        ],
        "independent_error_origins": len({root for e in events for root in e["roots"]}),
        "runtime": runtime_metrics(candidate, run),
        "targets": {
            "source_support": "uncalibrated_95_percent",
            "critical_and_qualification_retention": "uncalibrated_90_percent",
        },
    }


def markdown(data):
    require(data.get("version") == REPORT and data.get("mode") == "synthetic_reference")
    require(data["status"] in ("provisional", "reviewed_measurement"))
    require(type(data["review"]["missing"]) is int and data["review"]["missing"] >= 0)
    from .validation import unique

    names = (
        "upstream_support",
        "source_support",
        "kind_correct",
        "critical_fact_retention",
        "qualification_retention",
        "end_to_end_retention",
        "relation_classification",
        "conflict_recall",
        "conflict_precision",
        "severe_candidate_incidence",
    )
    require(set(data["observation_metrics"]) <= set(names))
    safe = {}
    for name, metric in {**data["observation_metrics"], "acceptance": data["acceptance"]}.items():
        # Rebuild from safe numeric values, rejecting injected prose/extra fields.
        require(set(metric) == {"success", "N", "J", "I", "U", "NA", "rate", "coverage", "status"})
        require(
            all(
                type(metric[k]) is int and metric[k] >= 0
                for k in ("success", "N", "J", "I", "U", "NA")
            )
        )
        require(
            metric["N"] == metric["J"] + metric["I"] + metric["U"]
            and metric["success"] <= metric["J"]
        )
        require(metric["rate"] == (f"{metric['success']}/{metric['J']}" if metric["J"] else None))
        require(metric["coverage"] == (f"{metric['J']}/{metric['N']}" if metric["N"] else None))
        require(
            metric["status"]
            == (
                "provisional"
                if metric["I"] or metric["U"]
                else "complete"
                if metric["N"]
                else "not_applicable"
            )
        )
        safe[name] = metric
    unique(list(safe))
    # Project fields; do not interpolate arbitrary annotation reasons or input text.
    return (
        "# Synthetic evaluator report\n\n"
        f"Status: {data['status']}\n\n"
        "Real model quality: NOT TESTED.\n\n"
        f"Independent human review outstanding: {data['review']['missing']}.\n\n"
        "Counts include N/J/I/U/NA; no composite score.\n\n"
        "Acceptance counts reflect annotation judgments, not formal human acceptance.\n\n"
        + "```json\n"
        + encode(
            {
                "metrics": {k: v for k, v in safe.items() if k != "acceptance"},
                "acceptance": safe["acceptance"],
            }
        )
        + "\n```\n"
    )
