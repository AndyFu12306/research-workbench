"""Referential integrity for evaluation data, not a semantic oracle."""

import re

from research_workbench.analysis.chunking import (
    ChunkingConfig,
    SourceDocument,
    SourceIdentity,
    chunk_document,
)

from .contracts import ATOMIZATION, BENCHMARK, RUBRIC, require
from .loading import canonical, text_hash, typed

LAYERS = ("map_collection", "document_reduce", "cross_document_reduce")


def unique(values):
    require(len(values) == len(set(values)))


def identifier(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_./-]{1,160}", value))


def evidence(case, spans):
    sources = {s.alias: s for s in case.sources}
    for span in spans:
        require(span.source in sources)
        text = sources[span.source].text
        require(type(span.start) is int and type(span.end) is int)
        require(0 <= span.start < span.end <= len(text))
        require(text_hash(text[span.start : span.end]) == span.sha256)


def chunks_for(case):
    return {
        s.alias: chunk_document(
            SourceDocument(SourceIdentity(*s.identity), s.text),
            ChunkingConfig(case.max_chars, case.overlap_chars),
        )
        for s in case.sources
    }


def layout(case):
    """Actual production chunker defines the test script's expected positions."""
    result = {}
    for source, chunks in chunks_for(case).items():
        maps = []
        for c in chunks:
            alias = f"{source}/map/{c.ordinal}"
            result[alias] = (f"{source}/chunk/{c.ordinal}",)
            maps.append(alias)
        result[f"{source}/document"] = tuple(maps)
    result["cross"] = tuple(f"{s.alias}/document" for s in case.sources)
    return result


def validate_case(case):
    typed(type(case), canonical(case))
    require(case.version == BENCHMARK and case.revision > 0)
    identifier(case.case_id)
    require(re.fullmatch(r"Q(?:0[1-9]|1[0-8])", case.case_id))
    require(case.question.version == "quality-question-v1" and bool(case.question.title.strip()))
    require(case.requirements_version == "quality-requirements-v2" and 2 <= len(case.sources) <= 5)
    unique([s.alias for s in case.sources])
    unique([s.identity for s in case.sources])
    for s in case.sources:
        identifier(s.alias)
        require(s.identity[:2] == ("zotero", "personal") and s.identity[2].startswith("FAKE"))
        require(bool(s.text.strip()) and text_hash(s.text) == s.sha256)
    require(case.max_chars > 0 and 0 <= case.overlap_chars < case.max_chars)
    unique([f.fact_id for f in case.facts])
    for f in case.facts:
        identifier(f.fact_id)
        require(bool(f.proposition.strip()) and bool(f.evidence) and bool(f.stages))
        unique(f.stages)
        evidence(case, f.evidence)
    unique([r.relation_id for r in case.relations])
    for r in case.relations:
        identifier(r.relation_id)
        unique(r.facts)
        require(len(r.facts) >= 2 and set(r.facts) <= {f.fact_id for f in case.facts})
        require(bool(r.expected.strip()))
    unique([t.target_id for t in case.acceptance_targets])
    for target in case.acceptance_targets:
        identifier(target.target_id)
        require(target.target_id.startswith("acceptance/") and bool(target.criterion.strip()))


def validate_candidate(case, candidate):
    typed(type(candidate), canonical(candidate))
    require(candidate.version == "quality-candidate-v1" and candidate.revision > 0)
    require(candidate.case_id == case.case_id and candidate.max_calls > 0)
    require(candidate.stage_input_limit > 0)
    identifier(candidate.candidate_id)
    require(re.fullmatch(case.case_id + r"_[a-z_]+", candidate.candidate_id))
    planned = layout(case)
    require(tuple(s.alias for s in candidate.stages) == tuple(planned))
    known, owners = {}, {}
    for stage in candidate.stages:
        unique([f.alias for f in stage.findings])
        require(len(stage.findings) <= 12)
        if "/map/" in stage.alias:
            allowed = set(planned[stage.alias])
        else:
            allowed = {f for f, owner in owners.items() if owner in planned[stage.alias]}
        for f in stage.findings:
            identifier(f.alias)
            require(f.alias.startswith(stage.alias + "/") and f.alias not in known)
            require(bool(f.text.strip()) and len(f.text) <= 2000 and 1 <= len(f.citations) <= 20)
            unique(f.citations)
            require(set(f.citations) <= allowed)
            known[f.alias], owners[f.alias] = f, stage.alias


def finding_sources(case, candidate):
    result = {}
    for stage in candidate.stages:
        for f in stage.findings:
            result[f.alias] = set().union(
                *({ref.split("/")[0]} if "/chunk/" in ref else result[ref] for ref in f.citations)
            )
    return result


def review_targets(case, assessment):
    # Risk flags prioritize review; every scored object still needs both reviewers.
    # Finding atomization and individual Claim semantics are separate attestations.
    targets = set(assessment.atomized_findings)
    targets.update(j.target_id for j in assessment.acceptance_judgments)
    targets.update(c.claim_id for c in assessment.claims)
    targets.update(f"{r.fact_id}/{r.layer}" for r in assessment.retentions)
    targets.update(r.relation_id for r in case.relations)
    return targets


def validate_source_support(claim):
    """Reject a categorical contradiction, without voting or inferring support."""
    if claim.source_support == "supported":
        require(bool(claim.citations))
        require(not all(j.stance in ("opposes", "irrelevant") for j in claim.citations))


def validate_assessment(case, candidate, assessment):
    typed(type(assessment), canonical(assessment))
    require(assessment.version == "quality-assessment-v2" and assessment.revision > 0)
    require(assessment.candidate_id == candidate.candidate_id)
    require(assessment.rubric == RUBRIC and assessment.atomization == ATOMIZATION)
    identifier(assessment.assessment_id)
    expected_targets = {
        t.target_id for t in case.acceptance_targets if t.scenario == candidate.expected
    }
    unique([j.target_id for j in assessment.acceptance_judgments])
    require({j.target_id for j in assessment.acceptance_judgments} == expected_targets)
    for judgment in assessment.acceptance_judgments:
        require(judgment.verdict != "na" and bool(judgment.reason.strip()))
    if candidate.expected != "completed":
        require(not assessment.claims and not assessment.retentions and not assessment.relations)
        require(not assessment.atomized_findings)
        validate_reviews(case, assessment)
        return
    findings = {f.alias: f for s in candidate.stages for f in s.findings}
    stages = {f.alias: s.alias for s in candidate.stages for f in s.findings}
    unique([c.claim_id for c in assessment.claims])
    unique(assessment.atomized_findings)
    require(set(assessment.atomized_findings) == set(findings))
    require({c.finding for c in assessment.claims} == set(findings))
    closure = finding_sources(case, candidate)
    prior = {}
    stage_positions = {s.alias: i for i, s in enumerate(candidate.stages)}
    for c in assessment.claims:
        identifier(c.claim_id)
        require(c.finding in findings and stages[c.finding] == c.stage)
        text = findings[c.finding].text
        require(0 <= c.start < c.end <= len(text) and bool(c.reason.strip()))
        require(
            bool(c.upstream_refs) and set(c.upstream_refs) <= set(findings[c.finding].citations)
        )
        require(c.kind_correct != "na")
        evidence(case, c.source_evidence)
        require(bool(c.source_evidence))
        require({s.source for s in c.source_evidence} <= closure[c.finding])
        unique([j.source for j in c.citations])
        require({j.source for j in c.citations} == closure[c.finding])
        for j in c.citations:
            evidence(case, j.evidence)
            require(bool(j.evidence) and {s.source for s in j.evidence} == {j.source})
        validate_source_support(c)
        for p in c.predecessor_claims:
            require(p in prior and stage_positions[prior[p].stage] < stage_positions[c.stage])
            require(prior[p].finding in c.upstream_refs)
        require(c.link not in ("faithful", "changed", "combined") or bool(c.predecessor_claims))
        if c.link == "faithful":
            require(c.upstream_support in ("supported", "indeterminate", "unscored"))
        prior[c.claim_id] = c
    unique([(c.finding, c.start, c.end) for c in assessment.claims])
    expected = {(f.fact_id, layer) for f in case.facts for layer in f.stages}
    unique([(r.fact_id, r.layer) for r in assessment.retentions])
    require({(r.fact_id, r.layer) for r in assessment.retentions} == expected)
    chunk_refs = {ref for refs in layout(case).values() for ref in refs if "/chunk/" in ref}
    for r in assessment.retentions:
        require(bool(r.reason.strip()))
        refs = set(r.input_refs)
        fact = next(f for f in case.facts if f.fact_id == r.fact_id)
        required_sources = {s.source for s in fact.evidence}
        referenced_sources = {ref.split("/")[0] for ref in refs}
        require(referenced_sources <= required_sources)
        require(refs <= (chunk_refs | set(findings)))
        if r.layer == "map_collection":
            require(refs <= chunk_refs)
        elif r.layer == "document_reduce":
            require(all("/map/" in stages.get(ref, "") for ref in refs))
        else:
            require(all(stages.get(ref, "").endswith("/document") for ref in refs))
        if r.availability in ("explicit_complete", "collectively_sufficient"):
            require(bool(refs))
            require(referenced_sources == required_sources)
        if r.availability == "collectively_sufficient":
            require(len(refs) >= 2 and bool(r.counter_information.strip()))
    unique([r.relation_id for r in assessment.relations])
    require(
        {r.relation_id for r in assessment.relations} == {r.relation_id for r in case.relations}
    )
    for r in assessment.relations:
        require(r.classification != "na")
        original = next(x for x in case.relations if x.relation_id == r.relation_id)
        require((r.conflict_recalled == "na") == (original.kind != "conflict"))
        require((r.conflict_correct == "na") == (not r.conflict_asserted))
    validate_reviews(case, assessment)


def validate_reviews(case, assessment):
    targets = review_targets(case, assessment)
    for r in assessment.reviews:
        identifier(r.reviewer)
        require(bool(r.targets) and set(r.targets) <= targets and bool(r.note.strip()))
        unique(r.targets)
    unique([(r.reviewer, r.role) for r in assessment.reviews])
