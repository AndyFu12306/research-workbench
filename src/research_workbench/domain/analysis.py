"""Immutable analysis history and provenance; never raw source content."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AnalysisSourceInput:
    analysis_id: int
    source_id: int
    ordinal: int
    input_kind: str
    content_sha256: str
    input_chars: int


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    id: int
    question_id: int
    status: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    result_json: str | None
    provider_response_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
