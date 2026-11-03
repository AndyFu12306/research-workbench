"""Source identity and associations only: never bibliographic content."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime, utc_now


class SourceRow(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("provider", "scope", "external_key", name="uq_sources_identity"),
        CheckConstraint("provider = 'zotero'", name="ck_sources_provider"),
        CheckConstraint("length(trim(scope)) BETWEEN 1 AND 128", name="ck_sources_scope"),
        CheckConstraint(
            "length(external_key) = 8 AND external_key NOT GLOB '*[^A-Z0-9]*'",
            name="ck_sources_external_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(128))
    external_key: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=func.current_timestamp()
    )


class QuestionSourceRow(Base):
    __tablename__ = "question_sources"

    # Composite primary key provides UNIQUE(question_id, source_id) without a redundant index.
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", name="fk_question_sources_question_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", name="fk_question_sources_source_id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=func.current_timestamp()
    )
