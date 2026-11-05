"""Frozen test DTOs. Semantic values are annotations, never inferred from text."""

from dataclasses import dataclass, field
from typing import Literal

BENCHMARK = "research-quality-benchmark-v1"
RUBRIC = "quality-rubric-v1"
REPORT = "quality-report-v1"
BINDING = "quality-observation-binding-v1"
ADAPTER = "quality-observer-v1"
ATOMIZATION = "quality-atomization-v1"

Support = Literal[
    "supported", "partial", "unsupported", "contradicted", "indeterminate", "unscored"
]
Availability = Literal[
    "explicit_complete", "collectively_sufficient", "insufficient", "indeterminate", "unscored"
]
Preservation = Literal["preserved", "partial", "absent", "distorted", "indeterminate", "unscored"]
Verdict = Literal["pass", "fail", "indeterminate", "unscored", "na"]
Layer = Literal["map_collection", "document_reduce", "cross_document_reduce"]


class QualityError(ValueError):
    def __init__(self):
        super().__init__("Invalid quality data or observation binding.")


def require(condition):
    if not condition:
        raise QualityError()


@dataclass(frozen=True, slots=True)
class Span:
    source: str
    start: int
    end: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Source:
    alias: str
    identity: tuple[str, str, str]
    text: str = field(repr=False)
    sha256: str


@dataclass(frozen=True, slots=True)
class Question:
    version: str
    title: str = field(repr=False)
    description: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Fact:
    fact_id: str
    proposition: str = field(repr=False)
    subject: str
    method: str
    comparison: str
    result: str
    value_unit: str
    polarity: str
    conditions: str
    uncertainty: str
    evidence: tuple[Span, ...]
    critical: bool
    limitation: bool
    stages: tuple[Layer, ...]


@dataclass(frozen=True, slots=True)
class Relation:
    relation_id: str
    facts: tuple[str, ...]
    kind: Literal["support", "conflict", "conditional", "incomparable", "same_experiment"]
    conditions: str
    expected: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Case:
    version: str
    case_id: str
    revision: int
    language: Literal["zh", "en"]
    requirements_version: str
    question: Question
    sources: tuple[Source, ...]
    facts: tuple[Fact, ...]
    relations: tuple[Relation, ...]
    max_chars: int
    overlap_chars: int
    forbidden: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ScriptFinding:
    alias: str
    text: str = field(repr=False)
    kind: Literal["source_summary", "ai_inference"]
    category: Literal[
        "approach", "finding", "agreement", "disagreement", "limitation", "research_gap"
    ]
    citations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScriptStage:
    alias: str
    findings: tuple[ScriptFinding, ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    version: str
    candidate_id: str
    revision: int
    case_id: str
    stages: tuple[ScriptStage, ...]
    expected: Literal["completed", "preflight_rejected", "cross_rejected"]
    max_calls: int
    stage_input_limit: int


@dataclass(frozen=True, slots=True)
class CitationJudgment:
    source: str
    stance: Literal["supports", "opposes", "conditional", "irrelevant", "indeterminate"]
    evidence: tuple[Span, ...]


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    stage: str
    finding: str
    start: int
    end: int
    upstream_support: Support
    source_support: Support
    upstream_refs: tuple[str, ...]
    source_evidence: tuple[Span, ...]
    reason: str = field(repr=False)
    citations: tuple[CitationJudgment, ...]
    severity: Literal["none", "ordinary", "severe"]
    kind_correct: Verdict
    predecessor_claims: tuple[str, ...]
    link: Literal["root", "faithful", "changed", "combined", "new"]
    critical: bool


@dataclass(frozen=True, slots=True)
class Retention:
    fact_id: str
    layer: Layer
    availability: Availability
    outcome: Preservation
    input_refs: tuple[str, ...]
    reason: str = field(repr=False)
    counter_information: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RelationJudgment:
    relation_id: str
    classification: Verdict
    conflict_recalled: Verdict
    conflict_asserted: bool
    conflict_correct: Verdict


@dataclass(frozen=True, slots=True)
class Review:
    reviewer: str
    origin: Literal["human", "codex_draft", "mechanism_fixture"]
    role: Literal["primary", "secondary", "adjudicator"]
    targets: tuple[str, ...]
    agrees: bool
    note: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Assessment:
    version: str
    assessment_id: str
    revision: int
    candidate_id: str
    rubric: str
    atomization: str
    claims: tuple[Claim, ...]
    retentions: tuple[Retention, ...]
    relations: tuple[RelationJudgment, ...]
    atomized_findings: tuple[str, ...]
    reviews: tuple[Review, ...]


@dataclass(frozen=True, slots=True)
class Observation:
    stage: str
    input_sha256: str
    raw_sha256: str
    normalized_sha256: str
    # Explicit compact JSON projection, not a recursive runtime DTO snapshot.
    projection_json: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Binding:
    version: str
    run_alias: str
    case_sha256: str
    candidate_sha256: str
    rubric: str
    atomization: str
    adapter: str
    # Includes ordered actual observations and aliases; no wall clock / DB IDs.
    observation_sha256: str
    mapping_sha256: str
    config_sha256: str
    terminal: str
    digest: str


@dataclass(frozen=True, slots=True)
class BoundAssessment:
    binding: Binding
    assessment: Assessment = field(repr=False)
    claim_locations: tuple[tuple[str, str, str, int, int], ...]


@dataclass(frozen=True, slots=True)
class Opportunity:
    key: str
    verdict: Verdict
    reason: str = ""
