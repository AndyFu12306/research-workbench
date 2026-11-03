"""Analysis outputs are research data; source inputs are fingerprints only."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime, utc_now


class AnalysisRunRow(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','completed','failed')", name="ck_analysis_status"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_analysis_input_tokens"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_analysis_output_tokens"
        ),
        CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND result_json IS NOT NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_analysis_lifecycle",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str | None] = mapped_column(Text)
    provider_response_id: Mapped[str | None] = mapped_column(String(256))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=func.current_timestamp()
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(256))


class AnalysisSourceInputRow(Base):
    __tablename__ = "analysis_source_inputs"
    __table_args__ = (
        UniqueConstraint("analysis_id", "ordinal", name="uq_analysis_source_ordinal"),
        CheckConstraint("ordinal >= 1", name="ck_analysis_source_ordinal"),
        CheckConstraint(
            "input_kind IN ('abstract','fulltext','abstract_fallback')",
            name="ck_analysis_source_kind",
        ),
        CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_analysis_source_hash",
        ),
        CheckConstraint("input_chars > 0", name="ck_analysis_source_chars"),
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    input_kind: Mapped[str] = mapped_column(String(32))
    content_sha256: Mapped[str] = mapped_column(String(64))
    input_chars: Mapped[int] = mapped_column(Integer)
