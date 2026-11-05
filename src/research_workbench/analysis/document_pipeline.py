"""Bounded document execution configuration and safe, immutable history values."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .chunking import require_int, require_text
from .errors import FAILURE_MESSAGES
from .planning import MapPlan
from .stage_contracts import StageProvider, StageRequest, ValidatedStageResult

PIPELINE_ERRORS = {
    **FAILURE_MESSAGES,
    "invalid_plan": "Document plan or persisted completeness check failed.",
    "execution_budget_exceeded": "Document execution exceeds its configured budget.",
    "reduce_budget_exceeded": "Document Reduce exceeds its configured budget.",
    "interrupted": "Document execution was interrupted; the call outcome may be unknown.",
    "immutable_run": "Document history cannot be finalized again.",
    "source_unavailable": "The selected linked source has no verified complete fulltext.",
}


class DocumentAnalysisError(ValueError):
    def __init__(self, code="invalid_plan"):
        self.code = code if code in PIPELINE_ERRORS else "analysis_failed"
        super().__init__(PIPELINE_ERRORS[self.code])


def render_document_question(title: str, description: str) -> str:
    """Canonical, ephemeral Question snapshot shared by planning and persistence checks."""
    if type(title) is not str or type(description) is not str:
        raise DocumentAnalysisError("invalid_plan")
    rendered = json.dumps(
        {"title": title, "description": description},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    require_text(rendered)
    return rendered


@dataclass(frozen=True, slots=True)
class Pricing:
    """Explicit nominal per-million-token prices; never a billing guarantee."""

    currency: str
    version: str
    input_per_million: Decimal
    output_per_million: Decimal

    def __post_init__(self):
        if (
            type(self.currency) is not str
            or type(self.version) is not str
            or not self.currency.isascii()
            or not self.currency.isalpha()
            or len(self.currency) != 3
            or not 1 <= len(self.version) <= 64
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for c in self.version
            )
            or any(
                type(v) is not Decimal or not v.is_finite() or v < 0
                for v in (self.input_per_million, self.output_per_million)
            )
        ):
            raise DocumentAnalysisError("execution_budget_exceeded")

    def cost(self, input_tokens: int | None, output_tokens: int | None) -> Decimal | None:
        if input_tokens is None or output_tokens is None:
            return None
        return (
            Decimal(input_tokens) * self.input_per_million
            + Decimal(output_tokens) * self.output_per_million
        ) / Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    max_calls: int = 101
    max_estimated_tokens: int = 1_000_000
    reduce_reserved_tokens: int = 32_768
    max_estimated_cost: Decimal | None = None

    def __post_init__(self):
        for v in (self.max_calls, self.max_estimated_tokens, self.reduce_reserved_tokens):
            require_int(v)
        if self.reduce_reserved_tokens > self.max_estimated_tokens or (
            self.max_estimated_cost is not None
            and (
                type(self.max_estimated_cost) is not Decimal
                or not self.max_estimated_cost.is_finite()
                or self.max_estimated_cost < 0
            )
        ):
            raise DocumentAnalysisError("execution_budget_exceeded")


@dataclass(frozen=True, slots=True)
class RequestEstimate:
    input_tokens: int
    context_tokens: int
    estimator_version: str = "stage-request-utf8-bytes-v1"


class StageEstimator(Protocol):
    def estimate_stage(self, request: StageRequest) -> RequestEstimate: ...


class BudgetedStageProvider(StageProvider, StageEstimator, Protocol):
    """One injected adapter owns both final-parameter estimation and the single call."""


@dataclass(frozen=True, slots=True)
class DocumentRun:
    id: int
    question_id: int
    source_id: int
    status: str
    provider: str
    model: str
    source_fingerprint: str
    content_sha256: str
    input_chars: int
    question_sha256: str
    chunk_count: int
    config_json: str = field(repr=False)
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ChunkManifest:
    execution_id: int
    ordinal: int
    chunk_id: str
    start: int
    end: int
    content_sha256: str
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class StageRun:
    execution_id: int
    ordinal: int
    stage: str
    status: str
    prompt_version: str
    contract_version: str
    request_sha256: str | None
    estimated_input_tokens: int | None
    estimated_context_tokens: int | None
    estimator_version: str | None
    estimated_cost: str | None
    input_tokens: int | None
    output_tokens: int | None
    usage_cost: str | None
    response_id: str | None
    result_json: str | None = field(repr=False)
    call_outcome: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class StageInput:
    execution_id: int
    parent_ordinal: int
    ordinal: int
    chunk_ordinal: int | None
    child_ordinal: int | None


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    plan: MapPlan = field(repr=False)
    requests: tuple[StageRequest, ...] = field(repr=False)
    estimates: tuple[RequestEstimate, ...]


@dataclass(frozen=True, slots=True)
class DocumentExecutionOutcome:
    run: DocumentRun
    final_stage_ordinal: int
    result: ValidatedStageResult = field(repr=False)
