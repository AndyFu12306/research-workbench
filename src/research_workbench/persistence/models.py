"""Internal ORM mappings: only Workbench Project and Question are persisted."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite stores naive UTC; callers always receive an aware UTC datetime."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 300", name="ck_projects_title"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(8), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=func.current_timestamp(), onupdate=utc_now
    )
    questions: Mapped[list["QuestionRow"]] = relationship(
        back_populates="project", passive_deletes="all"
    )


class QuestionRow(Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 300", name="ck_questions_title"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_questions_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", name="fk_questions_project_id_projects", ondelete="RESTRICT"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(8), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=func.current_timestamp(), onupdate=utc_now
    )
    project: Mapped[ProjectRow] = relationship(back_populates="questions")
