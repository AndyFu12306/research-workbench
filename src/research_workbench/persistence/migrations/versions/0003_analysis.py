"""Immutable analysis history and source fingerprint manifest."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("questions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text()),
        sa.Column("provider_response_id", sa.String(256)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(256)),
        sa.CheckConstraint("status IN ('running','completed','failed')", name="ck_analysis_status"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_analysis_input_tokens"
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_analysis_output_tokens"
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND result_json IS NOT NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_analysis_lifecycle",
        ),
    )
    op.create_index("ix_analysis_runs_question_id", "analysis_runs", ["question_id"])
    op.create_table(
        "analysis_source_inputs",
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("analysis_runs.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input_kind", sa.String(32), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("input_chars", sa.Integer(), nullable=False),
        sa.UniqueConstraint("analysis_id", "ordinal", name="uq_analysis_source_ordinal"),
        sa.CheckConstraint("ordinal >= 1", name="ck_analysis_source_ordinal"),
        sa.CheckConstraint(
            "input_kind IN ('abstract','fulltext','abstract_fallback')",
            name="ck_analysis_source_kind",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_analysis_source_hash",
        ),
        sa.CheckConstraint("input_chars > 0", name="ck_analysis_source_chars"),
    )
    op.create_index("ix_analysis_source_inputs_source_id", "analysis_source_inputs", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_source_inputs_source_id", table_name="analysis_source_inputs")
    op.drop_table("analysis_source_inputs")
    op.drop_index("ix_analysis_runs_question_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")
