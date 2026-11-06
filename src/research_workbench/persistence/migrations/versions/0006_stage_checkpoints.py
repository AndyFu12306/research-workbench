"""Append lossless generated checkpoints and single-claim resume lineage."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stage_checkpoints",
        sa.Column(
            "run_id", sa.Integer, sa.ForeignKey("cross_document_analysis_runs.id"), primary_key=True
        ),
        sa.Column("member", sa.Integer, primary_key=True),
        sa.Column("ordinal", sa.Integer, primary_key=True),
        sa.Column("output_json", sa.Text, nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("projection_sha256", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("source_run_id", sa.Integer, sa.ForeignKey("cross_document_analysis_runs.id")),
    )
    op.create_table(
        "execution_resumes",
        sa.Column(
            "source_run_id",
            sa.Integer,
            sa.ForeignKey("cross_document_analysis_runs.id"),
            primary_key=True,
        ),
        sa.Column(
            "run_id",
            sa.Integer,
            sa.ForeignKey("cross_document_analysis_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("new_calls", sa.Integer, nullable=False),
        sa.Column("new_context_units", sa.Integer, nullable=False),
        sa.Column("new_cost", sa.String(128)),
    )


def downgrade():
    op.drop_table("execution_resumes")
    op.drop_table("stage_checkpoints")
