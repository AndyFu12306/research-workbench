"""Scoped source references and Question associations; no metadata columns."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("external_key", sa.String(8), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("provider", "scope", "external_key", name="uq_sources_identity"),
        sa.CheckConstraint("provider = 'zotero'", name="ck_sources_provider"),
        sa.CheckConstraint("length(trim(scope)) BETWEEN 1 AND 128", name="ck_sources_scope"),
        sa.CheckConstraint(
            "length(external_key) = 8 AND external_key NOT GLOB '*[^A-Z0-9]*'",
            name="ck_sources_external_key",
        ),
    )
    op.create_table(
        "question_sources",
        sa.Column("question_id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["questions.id"],
            name="fk_question_sources_question_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_question_sources_source_id", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_question_sources_source_id", "question_sources", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_question_sources_source_id", table_name="question_sources")
    op.drop_table("question_sources")
    op.drop_table("sources")
