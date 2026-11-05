"""Offline Map input plans, separate from the Phase 4A request/provider path."""

import json
from dataclasses import dataclass, field

from .chunking import (
    ChunkingConfig,
    PlanningInputError,
    PlanningLimitError,
    SourceDocument,
    TextChunk,
    chunk_document,
    require_int,
    require_text,
)

ESTIMATOR_VERSION = "utf8-bytes-v1"
MAP_ENVELOPE_VERSION = "map-plan-envelope-v1"


def estimate_tokens(text: str) -> int:
    """One UTF-8 byte per estimated token, NOT a model tokenizer or guarantee."""
    require_text(text)
    return len(text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class MapTemplate:
    """Caller-supplied planning material; not an executable Map output contract."""

    instructions: str = field(repr=False)
    format_spec: str = field(repr=False)

    def __post_init__(self):
        require_text(self.instructions)
        require_text(self.format_spec)


@dataclass(frozen=True, slots=True)
class PlanningBudget:
    context_tokens: int = 32_768
    max_input_tokens: int = 24_000
    reserved_output_tokens: int = 4_096
    safety_margin_tokens: int = 1_024
    protocol_overhead_tokens: int = 256
    max_sources: int = 100
    max_total_chars: int = 1_000_000
    max_total_chunks: int = 100
    max_map_calls: int = 100
    max_total_estimated_tokens: int = 1_000_000

    def __post_init__(self):
        for value in (
            self.context_tokens,
            self.max_input_tokens,
            self.reserved_output_tokens,
            self.max_sources,
            self.max_total_chars,
            self.max_total_chunks,
            self.max_map_calls,
            self.max_total_estimated_tokens,
        ):
            require_int(value)
        require_int(self.safety_margin_tokens, 0)
        require_int(self.protocol_overhead_tokens, 0)


@dataclass(frozen=True, slots=True)
class MapInput:
    chunk: TextChunk
    question: str = field(repr=False)
    template: MapTemplate = field(repr=False)
    envelope_version: str = MAP_ENVELOPE_VERSION

    def render(self) -> str:
        """Sensitive JSON for estimation only; not a provider API request."""
        return json.dumps(
            {
                "version": self.envelope_version,
                "instructions": self.template.instructions,
                "question": self.question,
                "chunk": {
                    "label": self.chunk.chunk_id,
                    "start": self.chunk.start,
                    "end": self.chunk.end,
                    "text": self.chunk.text,
                },
                "response_format": self.template.format_spec,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class PlannedMapCall:
    input: MapInput
    estimated_input_tokens: int
    reserved_output_tokens: int
    safety_margin_tokens: int

    @property
    def estimated_context_tokens(self) -> int:
        return self.estimated_input_tokens + self.reserved_output_tokens + self.safety_margin_tokens


@dataclass(frozen=True, slots=True)
class MapPlan:
    documents: tuple[SourceDocument, ...] = field(repr=False)
    calls: tuple[PlannedMapCall, ...] = field(repr=False)
    chunking: ChunkingConfig
    budget: PlanningBudget
    estimator_version: str = ESTIMATOR_VERSION

    @property
    def planned_map_calls(self) -> int:
        return len(self.calls)

    @property
    def total_estimated_tokens(self) -> int:
        """Includes per-call output reservations and safety margins, not actual usage."""
        return sum(call.estimated_context_tokens for call in self.calls)


class MapBudgetPlanner:
    def __init__(
        self, *, chunking: ChunkingConfig | None = None, budget: PlanningBudget | None = None
    ) -> None:
        self.chunking = chunking if chunking is not None else ChunkingConfig()
        self.budget = budget if budget is not None else PlanningBudget()
        if not isinstance(self.chunking, ChunkingConfig) or not isinstance(
            self.budget, PlanningBudget
        ):
            raise PlanningInputError()

    def build(
        self, question: str, documents: tuple[SourceDocument, ...], template: MapTemplate
    ) -> MapPlan:
        require_text(question)
        if (
            type(documents) is not tuple
            or not documents
            or any(not isinstance(doc, SourceDocument) for doc in documents)
            or not isinstance(template, MapTemplate)
        ):
            raise PlanningInputError()
        budget = self.budget
        if (
            len(documents) > budget.max_sources
            or sum(len(doc.text) for doc in documents) > budget.max_total_chars
        ):
            raise PlanningLimitError()
        if len({doc.identity for doc in documents}) != len(documents):
            # One current snapshot per source. Do not silently deduplicate or mix versions.
            raise PlanningInputError()
        calls = []
        total = 0
        for document in documents:
            remaining = budget.max_total_chunks - len(calls)
            if remaining < 1:
                raise PlanningLimitError()
            chunks = chunk_document(document, self.chunking, max_chunks=remaining)
            for chunk in chunks:
                if len(calls) >= budget.max_map_calls:
                    raise PlanningLimitError()
                candidate = MapInput(chunk, question, template)
                estimated_input = (
                    estimate_tokens(candidate.render()) + budget.protocol_overhead_tokens
                )
                call = PlannedMapCall(
                    candidate,
                    estimated_input,
                    budget.reserved_output_tokens,
                    budget.safety_margin_tokens,
                )
                total += call.estimated_context_tokens
                if (
                    estimated_input > budget.max_input_tokens
                    or call.estimated_context_tokens > budget.context_tokens
                    or total > budget.max_total_estimated_tokens
                ):
                    # No partial plan, automatic smaller chunks, truncation or retries.
                    raise PlanningLimitError()
                calls.append(call)
        return MapPlan(documents, tuple(calls), self.chunking, budget)
