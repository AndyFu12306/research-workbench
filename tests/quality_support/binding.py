"""Versioned offline observation binding. Hashes detect mismatch, not authenticity."""

from dataclasses import replace

from .contracts import ADAPTER, ATOMIZATION, BINDING, RUBRIC, Binding, BoundAssessment, require
from .loading import decode, digest, text_hash
from .scripted import actual_findings
from .validation import (
    identifier,
    layout,
    unique,
    validate_assessment,
    validate_candidate,
    validate_case,
)


def make_binding(case, candidate, run):
    validate_case(case)
    validate_candidate(case, candidate)
    require(run.case_sha256 == digest(case) and run.candidate_sha256 == digest(candidate))
    identifier(run.run_alias)
    require(
        run.terminal
        in ("completed", "preflight_rejected", "cross_rejected", "failed", "interrupted")
    )
    planned = tuple(layout(case))
    actual = tuple(o.stage for o in run.observations)
    require(actual == planned[: len(actual)])
    if run.terminal == "completed":
        require(actual == planned)
    if run.terminal == "preflight_rejected":
        require(not actual)
    if run.terminal == "cross_rejected":
        require(actual == planned[:-1])
    findings = actual_findings(run)
    expected_mapping = tuple(
        (o.stage, f["alias"], f["finding_id"])
        for o in run.observations
        for f in decode(o.projection_json)["findings"]
    )
    require(run.mapping == expected_mapping)
    unique([alias for _, alias, _ in run.mapping])
    unique([(stage, fid) for stage, _, fid in run.mapping])
    for stage, alias, fid in run.mapping:
        identifier(stage)
        identifier(alias)
        require(
            alias.startswith(stage + "/")
            and len(fid) == 64
            and all(c in "0123456789abcdef" for c in fid)
        )
    for o in run.observations:
        for value in (o.input_sha256, o.raw_sha256, o.normalized_sha256):
            require(len(value) == 64 and all(c in "0123456789abcdef" for c in value))
    require(len(findings) == len(run.mapping))
    decode(run.config_json)
    base = Binding(
        BINDING,
        run.run_alias,
        digest(case),
        digest(candidate),
        RUBRIC,
        ATOMIZATION,
        ADAPTER,
        digest(run.observations),
        digest(run.mapping),
        digest(decode(run.config_json)),
        run.terminal,
        "",
    )
    return replace(base, digest=digest(base))


def _locations(case, candidate, run, assessment):
    validate_assessment(case, candidate, assessment)
    findings = actual_findings(run)
    if run.terminal != "completed":
        require(not assessment.claims)
        return ()
    require(set(findings) == set(assessment.atomized_findings))
    script_findings = {f.alias: f for s in candidate.stages for f in s.findings}
    # Old draft spans never silently attach to different actual returned prose or references.
    mapping = {alias: fid for _, alias, fid in run.mapping}
    for alias, (stage, f) in findings.items():
        scripted = script_findings[alias]
        require(
            (f["text"], f["kind"], f["category"])
            == (scripted.text, scripted.kind, scripted.category)
        )
        if "/map/" not in stage:
            require(f["citations"] == [mapping[r] for r in scripted.citations])
    return tuple(
        (
            c.claim_id,
            findings[c.finding][1]["finding_id"],
            text_hash(findings[c.finding][1]["text"]),
            c.start,
            c.end,
        )
        for c in assessment.claims
    )


def attach(case, candidate, run, assessment):
    """Explicitly create a new record; does not mark AI drafts human-reviewed."""
    require(run.terminal == candidate.expected)
    binding = make_binding(case, candidate, run)
    return BoundAssessment(binding, assessment, _locations(case, candidate, run, assessment))


def verify(case, candidate, run, bound):
    require(bound.binding == make_binding(case, candidate, run))
    require(
        bound.assessment.rubric == bound.binding.rubric
        and bound.assessment.atomization == bound.binding.atomization
    )
    require(bound.claim_locations == _locations(case, candidate, run, bound.assessment))
