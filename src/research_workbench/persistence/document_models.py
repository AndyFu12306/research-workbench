"""Four document-history tables. No source text or metadata columns."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)

from .models import Base, UTCDateTime

executions = Table(
    "document_analysis_runs",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "question_id",
        Integer,
        ForeignKey("questions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column(
        "source_id",
        Integer,
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column("status", String(16), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("model", String(256), nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("input_chars", Integer, nullable=False),
    Column("question_sha256", String(64), nullable=False),
    Column("chunk_count", Integer, nullable=False),
    Column("config_json", Text, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False, server_default=func.current_timestamp()),
    Column("completed_at", UTCDateTime()),
    Column("error_code", String(64)),
    CheckConstraint(
        "status IN ('running','completed','failed','interrupted')", name="ck_document_status"
    ),
    CheckConstraint("chunk_count BETWEEN 1 AND 100 AND input_chars > 0", name="ck_document_size"),
    CheckConstraint(
        "(status='running' AND completed_at IS NULL AND error_code IS NULL) OR "
        "(status='completed' AND completed_at IS NOT NULL AND error_code IS NULL) OR "
        "(status IN ('failed','interrupted') AND completed_at IS NOT NULL AND error_code "
        "IS NOT NULL)",
        name="ck_document_lifecycle",
    ),
    *[
        CheckConstraint(f"length({c})=64 AND {c} NOT GLOB '*[^0-9a-f]*'", name=f"ck_document_{c}")
        for c in ("source_fingerprint", "content_sha256", "question_sha256")
    ],
)

chunks = Table(
    "document_analysis_chunks",
    Base.metadata,
    Column(
        "execution_id",
        Integer,
        ForeignKey("document_analysis_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("ordinal", Integer, primary_key=True),
    Column("chunk_id", String(64), nullable=False),
    Column("start", Integer, nullable=False),
    Column("end", Integer, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    UniqueConstraint("execution_id", "chunk_id", name="uq_document_chunk"),
    CheckConstraint(
        "ordinal BETWEEN 1 AND 100 AND start >= 0 AND end > start", name="ck_document_chunk_range"
    ),
    *[
        CheckConstraint(
            f"length({c})=64 AND {c} NOT GLOB '*[^0-9a-f]*'", name=f"ck_document_chunk_{c}"
        )
        for c in ("source_fingerprint", "content_sha256", "chunk_id")
    ],
)

stages = Table(
    "analysis_stage_runs",
    Base.metadata,
    Column(
        "execution_id",
        Integer,
        ForeignKey("document_analysis_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("ordinal", Integer, primary_key=True),
    Column("stage", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("prompt_version", String(64), nullable=False),
    Column("contract_version", String(64), nullable=False),
    Column("request_sha256", String(64)),
    Column("estimated_input_tokens", Integer),
    Column("estimated_context_tokens", Integer),
    Column("estimator_version", String(64)),
    Column("estimated_cost", String(128)),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("usage_cost", String(128)),
    Column("response_id", String(256)),
    Column("result_json", Text),
    Column("call_outcome", String(16), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False, server_default=func.current_timestamp()),
    Column("started_at", UTCDateTime()),
    Column("completed_at", UTCDateTime()),
    Column("error_code", String(64)),
    CheckConstraint(
        "stage IN ('map','document_reduce') AND ordinal BETWEEN 1 AND 101", name="ck_stage_kind"
    ),
    CheckConstraint(
        "status IN ('planned','running','completed','failed','interrupted','not_started')",
        name="ck_stage_status",
    ),
    CheckConstraint(
        "call_outcome IN ('not_started','unknown','response_received')", name="ck_stage_outcome"
    ),
    CheckConstraint(
        "(status='planned' AND started_at IS NULL AND completed_at IS NULL AND "
        "result_json IS NULL AND error_code IS NULL AND call_outcome='not_started') OR "
        "(status='running' AND started_at IS NOT NULL AND completed_at IS NULL AND "
        "result_json IS NULL AND error_code IS NULL AND call_outcome='unknown' AND "
        "request_sha256 IS NOT NULL AND estimated_input_tokens IS NOT NULL AND "
        "estimated_context_tokens IS NOT NULL AND estimator_version IS NOT NULL) OR "
        "(status='completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL AND "
        "result_json IS NOT NULL AND error_code IS NULL AND call_outcome='response_received') OR "
        "(status IN ('failed','interrupted') AND started_at IS NOT NULL AND completed_at "
        "IS NOT NULL "
        "AND result_json IS NULL AND error_code IS NOT NULL AND call_outcome IN "
        "('unknown','response_received')) OR "
        "(status='not_started' AND started_at IS NULL AND completed_at IS NOT NULL AND "
        "result_json IS NULL AND error_code IS NOT NULL AND call_outcome='not_started')",
        name="ck_stage_lifecycle",
    ),
    *[
        CheckConstraint(f"{c} IS NULL OR {c} >= 0", name=f"ck_stage_{c}")
        for c in (
            "input_tokens",
            "output_tokens",
            "estimated_input_tokens",
            "estimated_context_tokens",
        )
    ],
)

inputs = Table(
    "analysis_stage_inputs",
    Base.metadata,
    Column("execution_id", Integer, primary_key=True),
    Column("parent_ordinal", Integer, primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("chunk_ordinal", Integer),
    Column("child_ordinal", Integer),
    ForeignKeyConstraint(
        ["execution_id", "parent_ordinal"],
        ["analysis_stage_runs.execution_id", "analysis_stage_runs.ordinal"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["execution_id", "child_ordinal"],
        ["analysis_stage_runs.execution_id", "analysis_stage_runs.ordinal"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["execution_id", "chunk_ordinal"],
        ["document_analysis_chunks.execution_id", "document_analysis_chunks.ordinal"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("execution_id", "parent_ordinal", "child_ordinal", name="uq_stage_child"),
    CheckConstraint(
        "ordinal >= 1 AND ((chunk_ordinal IS NOT NULL AND child_ordinal IS NULL) OR "
        "(chunk_ordinal IS NULL AND child_ordinal IS NOT NULL AND child_ordinal < "
        "parent_ordinal))",
        name="ck_stage_input",
    ),
)
