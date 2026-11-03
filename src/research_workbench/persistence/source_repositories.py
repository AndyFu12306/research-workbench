"""No Zotero imports/calls: sources and links are ordinary Workbench data."""

import re

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from research_workbench.domain import QuestionSource, SourceRef

from .database import Database
from .errors import EntityNotFoundError
from .models import QuestionRow
from .repositories import _identifier, _pagination
from .source_models import QuestionSourceRow, SourceRow


def _identity(provider: str, scope: str, external_key: str) -> tuple[str, str, str]:
    if provider != "zotero":
        raise ValueError("Only the zotero provider is supported.")
    if not isinstance(scope, str) or not 1 <= len(scope.strip()) <= 128:
        raise ValueError("scope must contain 1 to 128 non-padding characters.")
    if not isinstance(external_key, str) or not re.fullmatch(r"[A-Z0-9]{8}", external_key):
        raise ValueError("external_key must be an 8-character uppercase Zotero item key.")
    return provider, scope.strip(), external_key


def _reference(row: SourceRow) -> SourceRef:
    return SourceRef(row.id, row.provider, row.scope, row.external_key, row.created_at)


def _require_question(session: Session, question_id: int) -> None:
    if session.get(QuestionRow, _identifier(question_id)) is None:
        raise EntityNotFoundError("Question", question_id)


def _get_or_create(session: Session, provider: str, scope: str, external_key: str) -> SourceRow:
    session.execute(
        insert(SourceRow)
        .values(provider=provider, scope=scope, external_key=external_key)
        .on_conflict_do_nothing(index_elements=["provider", "scope", "external_key"])
    )
    return session.scalars(
        select(SourceRow).where(
            SourceRow.provider == provider,
            SourceRow.scope == scope,
            SourceRow.external_key == external_key,
        )
    ).one()


def _attach(session: Session, question_id: int, source: SourceRow) -> QuestionSource:
    session.execute(
        insert(QuestionSourceRow)
        .values(question_id=question_id, source_id=source.id)
        .on_conflict_do_nothing(index_elements=["question_id", "source_id"])
    )
    row = session.get(QuestionSourceRow, (question_id, source.id))
    return QuestionSource(row.question_id, _reference(source), row.created_at)


class SourceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, source_id: int) -> SourceRef | None:
        with self._database._session() as session:
            row = session.get(SourceRow, _identifier(source_id))
            return _reference(row) if row is not None else None

    def get_or_create_reference(
        self, external_key: str, *, provider: str = "zotero", scope: str = "personal"
    ) -> SourceRef:
        identity = _identity(provider, scope, external_key)
        with self._database._session() as session:
            return _reference(_get_or_create(session, *identity))


class QuestionSourceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def attach(self, question_id: int, source_id: int) -> QuestionSource:
        with self._database._session() as session:
            _require_question(session, question_id)
            source = session.get(SourceRow, _identifier(source_id))
            if source is None:
                raise EntityNotFoundError("SourceRef", source_id)
            return _attach(session, question_id, source)

    def attach_reference(
        self,
        question_id: int,
        external_key: str,
        *,
        provider: str = "zotero",
        scope: str = "personal",
    ) -> QuestionSource:
        """Atomically create/reuse a reference and link it; rollback both on failure."""
        identity = _identity(provider, scope, external_key)
        with self._database._session() as session:
            _require_question(session, question_id)
            return _attach(session, question_id, _get_or_create(session, *identity))

    def list_for_question(
        self, question_id: int, *, limit: int = 100, offset: int = 0
    ) -> tuple[QuestionSource, ...]:
        _pagination(limit, offset)
        with self._database._session() as session:
            _require_question(session, question_id)
            rows = session.execute(
                select(QuestionSourceRow, SourceRow)
                .join(SourceRow, SourceRow.id == QuestionSourceRow.source_id)
                .where(QuestionSourceRow.question_id == question_id)
                .order_by(QuestionSourceRow.source_id)
                .limit(limit)
                .offset(offset)
            )
            return tuple(
                QuestionSource(row.question_id, _reference(source), row.created_at)
                for row, source in rows
            )

    def detach(self, question_id: int, source_id: int) -> bool:
        """Delete only this association; keep SourceRef and never contact Zotero."""
        source_id = _identifier(source_id)
        with self._database._session() as session:
            _require_question(session, question_id)
            result = session.execute(
                delete(QuestionSourceRow).where(
                    QuestionSourceRow.question_id == question_id,
                    QuestionSourceRow.source_id == source_id,
                )
            )
            return result.rowcount > 0
