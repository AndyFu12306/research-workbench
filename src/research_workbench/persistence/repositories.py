"""Small, explicit repositories. No generic framework and no hard-delete API."""

from sqlalchemy import select

from research_workbench.domain import Project, Question, ResearchStatus

from .database import Database
from .errors import EntityNotFoundError
from .models import ProjectRow, QuestionRow, utc_now


def _identifier(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("ID must be a positive integer.")
    return value


def _title(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 300:
        raise ValueError("Title must contain 1 to 300 non-padding characters.")
    return value.strip()


def _description(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Description must be a string.")
    return value


def _pagination(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000.")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer.")


def _project(row: ProjectRow) -> Project:
    return Project(
        row.id,
        row.title,
        row.description,
        ResearchStatus(row.status),
        row.created_at,
        row.updated_at,
    )


def _question(row: QuestionRow) -> Question:
    return Question(
        row.id,
        row.project_id,
        row.title,
        row.description,
        ResearchStatus(row.status),
        row.created_at,
        row.updated_at,
    )


class ProjectRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, title: str, description: str = "") -> Project:
        row = ProjectRow(title=_title(title), description=_description(description))
        with self._database._session() as session:
            session.add(row)
            session.flush()
            return _project(row)

    def get(self, project_id: int) -> Project | None:
        with self._database._session() as session:
            row = session.get(ProjectRow, _identifier(project_id))
            return _project(row) if row is not None else None

    def list(
        self, *, include_archived: bool = False, limit: int = 100, offset: int = 0
    ) -> tuple[Project, ...]:
        _pagination(limit, offset)
        statement = select(ProjectRow).order_by(ProjectRow.id).limit(limit).offset(offset)
        if not include_archived:
            statement = statement.where(ProjectRow.status == ResearchStatus.ACTIVE)
        with self._database._session() as session:
            return tuple(_project(row) for row in session.scalars(statement))

    def update(
        self, project_id: int, *, title: str | None = None, description: str | None = None
    ) -> Project:
        if title is None and description is None:
            raise ValueError("Provide a title or description to update.")
        new_title = _title(title) if title is not None else None
        new_description = _description(description) if description is not None else None
        with self._database._session() as session:
            row = session.get(ProjectRow, _identifier(project_id))
            if row is None:
                raise EntityNotFoundError("Project", project_id)
            if new_title is not None:
                row.title = new_title
            if new_description is not None:
                row.description = new_description
            row.updated_at = utc_now()
            session.flush()
            return _project(row)

    def archive(self, project_id: int) -> Project:
        """Archive this project only; do not delete or rewrite its questions."""
        with self._database._session() as session:
            row = session.get(ProjectRow, _identifier(project_id))
            if row is None:
                raise EntityNotFoundError("Project", project_id)
            if row.status != ResearchStatus.ARCHIVED:
                row.status = ResearchStatus.ARCHIVED
                row.updated_at = utc_now()
                session.flush()
            return _project(row)


class QuestionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, project_id: int, title: str, description: str = "") -> Question:
        row = QuestionRow(
            project_id=_identifier(project_id),
            title=_title(title),
            description=_description(description),
        )
        with self._database._session() as session:
            if session.get(ProjectRow, project_id) is None:
                raise EntityNotFoundError("Project", project_id)
            session.add(row)
            session.flush()
            return _question(row)

    def get(self, question_id: int) -> Question | None:
        with self._database._session() as session:
            row = session.get(QuestionRow, _identifier(question_id))
            return _question(row) if row is not None else None

    def list_by_project(
        self, project_id: int, *, include_archived: bool = False, limit: int = 100, offset: int = 0
    ) -> tuple[Question, ...]:
        _pagination(limit, offset)
        statement = (
            select(QuestionRow)
            .where(QuestionRow.project_id == _identifier(project_id))
            .order_by(QuestionRow.id)
            .limit(limit)
            .offset(offset)
        )
        if not include_archived:
            statement = statement.where(QuestionRow.status == ResearchStatus.ACTIVE)
        with self._database._session() as session:
            return tuple(_question(row) for row in session.scalars(statement))

    def update(
        self, question_id: int, *, title: str | None = None, description: str | None = None
    ) -> Question:
        if title is None and description is None:
            raise ValueError("Provide a title or description to update.")
        new_title = _title(title) if title is not None else None
        new_description = _description(description) if description is not None else None
        with self._database._session() as session:
            row = session.get(QuestionRow, _identifier(question_id))
            if row is None:
                raise EntityNotFoundError("Question", question_id)
            if new_title is not None:
                row.title = new_title
            if new_description is not None:
                row.description = new_description
            row.updated_at = utc_now()
            session.flush()
            return _question(row)

    def archive(self, question_id: int) -> Question:
        with self._database._session() as session:
            row = session.get(QuestionRow, _identifier(question_id))
            if row is None:
                raise EntityNotFoundError("Question", question_id)
            if row.status != ResearchStatus.ARCHIVED:
                row.status = ResearchStatus.ARCHIVED
                row.updated_at = utc_now()
                session.flush()
            return _question(row)
