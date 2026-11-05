"""Cross execution, exact member binding and one Cross stage; no source content."""

from alembic import op
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _hash(column, prefix, nullable=False):
    predicate = (
        f"typeof({column})='text' AND length({column})=64 AND {column} NOT GLOB '*[^0-9a-f]*'"
    )
    return CheckConstraint(
        (f"{column} IS NULL OR " if nullable else "") + predicate, name=f"ck_{prefix}_{column}"
    )


def _integer(column, lower, upper=None):
    predicate = f"typeof({column})='integer' AND {column}>={lower}"
    if upper is not None:
        predicate += f" AND {column}<={upper}"
    return predicate


def upgrade():
    op.create_table(
        "cross_document_analysis_runs",
        Column("id", Integer, primary_key=True, nullable=False),
        Column(
            "question_id",
            Integer,
            ForeignKey("questions.id", ondelete="RESTRICT", onupdate="RESTRICT"),
            nullable=False,
        ),
        Column("status", String(16), nullable=False),
        Column("provider", String(64), nullable=False),
        Column("model", String(256), nullable=False),
        Column("question_sha256", Text, nullable=False),
        Column("question_format_version", String(64), nullable=False),
        Column("plan_sha256", Text, nullable=False),
        Column("document_count", Integer, nullable=False),
        Column("config_json", Text, nullable=False),
        Column("owner_sha256", Text, nullable=False),
        Column("write_revision", Integer, nullable=False),
        Column("created_at", DateTime(), nullable=False, server_default=func.current_timestamp()),
        Column("completed_at", DateTime()),
        Column("error_code", String(64)),
        CheckConstraint(_integer("document_count", 2, 5), name="ck_cross_document_count"),
        CheckConstraint(_integer("write_revision", 0), name="ck_cross_revision"),
        CheckConstraint(
            "(status='running' AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status='completed' AND completed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status IN ('failed','interrupted') AND completed_at IS NOT NULL "
            "AND error_code IS NOT NULL)",
            name="ck_cross_lifecycle",
        ),
        *[_hash(c, "cross") for c in ("question_sha256", "plan_sha256", "owner_sha256")],
    )

    op.create_table(
        "cross_document_analysis_documents",
        Column(
            "run_id",
            Integer,
            ForeignKey("cross_document_analysis_runs.id", ondelete="RESTRICT", onupdate="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
        Column("ordinal", Integer, primary_key=True, nullable=False),
        Column(
            "source_id",
            Integer,
            ForeignKey("sources.id", ondelete="RESTRICT", onupdate="RESTRICT"),
            nullable=False,
        ),
        Column(
            "document_execution_id",
            Integer,
            ForeignKey("document_analysis_runs.id", ondelete="RESTRICT", onupdate="RESTRICT"),
            nullable=False,
        ),
        Column("final_stage_ordinal", Integer, nullable=False),
        Column("identity_sha256", Text, nullable=False),
        Column("source_fingerprint", Text, nullable=False),
        Column("content_sha256", Text, nullable=False),
        Column("input_chars", Integer, nullable=False),
        Column("read_at", DateTime(), nullable=False),
        UniqueConstraint("run_id", "source_id", name="uq_cross_member_source"),
        UniqueConstraint("document_execution_id", name="uq_cross_member_execution"),
        UniqueConstraint(
            "run_id",
            "ordinal",
            "document_execution_id",
            "final_stage_ordinal",
            name="uq_cross_member_binding",
        ),
        ForeignKeyConstraint(
            ["document_execution_id", "final_stage_ordinal"],
            ["analysis_stage_runs.execution_id", "analysis_stage_runs.ordinal"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        *[
            CheckConstraint(_integer(c, lo, hi), name=f"ck_cross_member_{c}")
            for c, lo, hi in (
                ("run_id", 1, None),
                ("source_id", 1, None),
                ("document_execution_id", 1, None),
                ("ordinal", 1, 5),
                ("final_stage_ordinal", 2, 101),
                ("input_chars", 1, None),
            )
        ],
        *[
            _hash(c, "cross_member")
            for c in ("identity_sha256", "source_fingerprint", "content_sha256")
        ],
    )

    op.create_table(
        "cross_document_stage_runs",
        Column(
            "run_id",
            Integer,
            ForeignKey("cross_document_analysis_runs.id", ondelete="RESTRICT", onupdate="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
        Column("ordinal", Integer, primary_key=True, nullable=False),
        Column("stage", String(32), nullable=False),
        Column("status", String(16), nullable=False),
        Column("prompt_version", String(64), nullable=False),
        Column("contract_version", String(64), nullable=False),
        Column("request_sha256", Text),
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
        Column("created_at", DateTime(), nullable=False, server_default=func.current_timestamp()),
        Column("started_at", DateTime()),
        Column("completed_at", DateTime()),
        Column("error_code", String(64)),
        CheckConstraint(
            _integer("ordinal", 1, 1) + " AND stage='cross_document_reduce'",
            name="ck_cross_stage_kind",
        ),
        CheckConstraint(_integer("run_id", 1), name="ck_cross_stage_parent"),
        CheckConstraint(
            "(status='planned' AND started_at IS NULL AND completed_at IS NULL "
            "AND result_json IS NULL "
            "AND error_code IS NULL AND call_outcome='not_started' AND request_sha256 IS NULL) OR "
            "(status='running' AND started_at IS NOT NULL AND completed_at IS "
            "NULL AND result_json IS NULL "
            "AND error_code IS NULL AND call_outcome='unknown' AND request_sha256 IS NOT NULL "
            "AND estimated_input_tokens IS NOT NULL AND estimated_context_tokens IS NOT NULL "
            "AND estimator_version IS NOT NULL) OR "
            "(status='completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND result_json IS NOT NULL AND error_code IS NULL AND "
            "call_outcome='response_received' "
            "AND request_sha256 IS NOT NULL AND estimated_input_tokens IS NOT NULL "
            "AND estimated_context_tokens IS NOT NULL AND estimator_version IS NOT NULL) OR "
            "(status IN ('failed','interrupted') AND started_at IS NOT NULL AND "
            "completed_at IS NOT NULL "
            "AND result_json IS NULL AND error_code IS NOT NULL AND call_outcome "
            "IN ('unknown','response_received')) OR "
            "(status='not_started' AND started_at IS NULL AND completed_at IS "
            "NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL AND call_outcome='not_started')",
            name="ck_cross_stage_lifecycle",
        ),
        *[
            CheckConstraint(f"{c} IS NULL OR (" + _integer(c, 0) + ")", name=f"ck_cross_stage_{c}")
            for c in (
                "estimated_input_tokens",
                "estimated_context_tokens",
                "input_tokens",
                "output_tokens",
            )
        ],
        _hash("request_sha256", "cross_stage", nullable=True),
    )

    op.create_table(
        "cross_document_stage_inputs",
        Column("run_id", Integer, primary_key=True, nullable=False),
        Column("parent_ordinal", Integer, primary_key=True, nullable=False),
        Column("input_ordinal", Integer, primary_key=True, nullable=False),
        Column("document_execution_id", Integer, nullable=False),
        Column("final_stage_ordinal", Integer, nullable=False),
        Column("child_request_sha256", Text),
        Column("child_result_sha256", Text),
        ForeignKeyConstraint(
            ["run_id", "parent_ordinal"],
            ["cross_document_stage_runs.run_id", "cross_document_stage_runs.ordinal"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "input_ordinal", "document_execution_id", "final_stage_ordinal"],
            [
                "cross_document_analysis_documents.run_id",
                "cross_document_analysis_documents.ordinal",
                "cross_document_analysis_documents.document_execution_id",
                "cross_document_analysis_documents.final_stage_ordinal",
            ],
            name="fk_cross_input_member",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "(child_request_sha256 IS NULL AND child_result_sha256 IS NULL) OR "
            "(child_request_sha256 IS NOT NULL AND child_result_sha256 IS NOT NULL)",
            name="ck_cross_input_hash_pair",
        ),
        *[
            CheckConstraint(_integer(c, lo, hi), name=f"ck_cross_input_{c}")
            for c, lo, hi in (
                ("run_id", 1, None),
                ("parent_ordinal", 1, 1),
                ("input_ordinal", 1, 5),
                ("document_execution_id", 1, None),
                ("final_stage_ordinal", 2, 101),
            )
        ],
        *[
            _hash(c, "cross_input", nullable=True)
            for c in ("child_request_sha256", "child_result_sha256")
        ],
    )


def downgrade():
    op.drop_table("cross_document_stage_inputs")
    op.drop_table("cross_document_stage_runs")
    op.drop_table("cross_document_analysis_documents")
    op.drop_table("cross_document_analysis_runs")
