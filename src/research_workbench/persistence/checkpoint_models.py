"""Generated JSON only; original source text and request payloads are never stored."""

from sqlalchemy import Column, ForeignKey, Integer, String, Table, Text

from .models import Base

checkpoints = Table(
    "stage_checkpoints",
    Base.metadata,
    Column("run_id", Integer, ForeignKey("cross_document_analysis_runs.id"), primary_key=True),
    Column("member", Integer, primary_key=True),  # 0 = cross reduce
    Column("ordinal", Integer, primary_key=True),
    Column("output_json", Text, nullable=False),
    Column("output_sha256", String(64), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("projection_sha256", String(64), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("source_run_id", Integer, ForeignKey("cross_document_analysis_runs.id")),
)

resumes = Table(
    "execution_resumes",
    Base.metadata,
    Column(
        "source_run_id", Integer, ForeignKey("cross_document_analysis_runs.id"), primary_key=True
    ),
    Column(
        "run_id",
        Integer,
        ForeignKey("cross_document_analysis_runs.id"),
        nullable=False,
        unique=True,
    ),
    Column("source_sha256", String(64), nullable=False),
    Column("new_calls", Integer, nullable=False),
    Column("new_context_units", Integer, nullable=False),
    Column("new_cost", String(128)),
)
