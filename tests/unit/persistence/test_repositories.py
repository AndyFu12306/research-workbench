from dataclasses import FrozenInstanceError
from datetime import UTC

import pytest

from research_workbench.domain import Project, Question, ResearchStatus
from research_workbench.persistence import (
    Database,
    EntityNotFoundError,
    ProjectRepository,
    QuestionRepository,
)
from research_workbench.persistence.models import ProjectRow, QuestionRow


def test_create_read_and_list_projects(projects):
    first = projects.create("  AI Agent 技术研究  ", "A research task, not a paper title.")
    second = projects.create("Another project")
    assert isinstance(first, Project)
    assert first.id > 0 and second.id > first.id
    assert first.title == "AI Agent 技术研究"
    assert first.description == "A research task, not a paper title."
    assert first.status is ResearchStatus.ACTIVE
    assert first.created_at.tzinfo is UTC and first.updated_at >= first.created_at
    assert projects.get(first.id) == first
    assert projects.list() == (first, second)
    assert projects.get(9999) is None
    with pytest.raises(FrozenInstanceError):
        first.title = "Cannot mutate a repository result"


def test_update_project_preserves_identity_creation_and_status(projects):
    original = projects.create("Original", "Original description")
    updated = projects.update(original.id, title="Revised", description="")
    assert updated.id == original.id and updated.created_at == original.created_at
    assert updated.updated_at > original.updated_at
    assert updated.status is ResearchStatus.ACTIVE
    assert updated.title == "Revised" and updated.description == ""
    assert projects.get(updated.id) == updated


def test_project_archive_is_idempotent_and_does_not_cascade(projects, questions):
    project = projects.create("Project")
    question = questions.create(project.id, "Question")
    archived = projects.archive(project.id)
    assert archived.status is ResearchStatus.ARCHIVED
    assert archived.created_at == project.created_at
    assert projects.list() == ()
    assert projects.list(include_archived=True) == (archived,)
    assert projects.get(project.id) == archived
    assert projects.archive(project.id) == archived
    assert questions.get(question.id) == question
    assert questions.list_by_project(project.id) == (question,)


def test_create_read_update_and_archive_question(projects, questions):
    project = projects.create("Project")
    original = questions.create(project.id, "Research question", "Description")
    assert isinstance(original, Question)
    assert original.project_id == project.id and original.status is ResearchStatus.ACTIVE
    assert questions.get(original.id) == original
    assert original.created_at.tzinfo is UTC
    updated = questions.update(original.id, title="Revised question", description="")
    assert updated.title == "Revised question" and updated.description == ""
    assert updated.project_id == project.id and updated.created_at == original.created_at
    assert updated.updated_at > original.updated_at
    archived = questions.archive(original.id)
    assert archived.status is ResearchStatus.ARCHIVED
    assert questions.archive(original.id) == archived
    assert questions.list_by_project(project.id) == ()
    assert questions.list_by_project(project.id, include_archived=True) == (archived,)
    assert questions.get(original.id) == archived
    assert questions.get(9999) is None


def test_questions_belong_to_exactly_one_project(database, projects, questions):
    first, second = projects.create("First"), projects.create("Second")
    a = questions.create(first.id, "First question")
    b = questions.create(first.id, "Second question")
    c = questions.create(second.id, "Other project question")
    assert questions.list_by_project(first.id) == (a, b)
    assert questions.list_by_project(second.id) == (c,)
    assert questions.list_by_project(9999) == ()
    with database._session() as session:
        parent = session.get(ProjectRow, first.id)
        assert {row.id for row in parent.questions} == {a.id, b.id}
        assert session.get(QuestionRow, c.id).project.id == second.id


def test_create_question_requires_existing_project(questions):
    with pytest.raises(EntityNotFoundError) as error:
        questions.create(9999, "Cannot be orphaned")
    assert error.value.entity == "Project"
    assert questions.list_by_project(9999) == ()


def test_data_survives_engine_disposal_and_new_repository(database, projects, questions):
    project = projects.create("Persisted")
    question = questions.create(project.id, "Persisted question")
    path = database.path
    database.close()
    with Database(path) as reopened:
        reopened.upgrade()
        assert ProjectRepository(reopened).get(project.id) == project
        assert QuestionRepository(reopened).get(question.id) == question


def test_pagination_is_stable_for_projects_and_questions(projects, questions):
    all_projects = [projects.create(f"Project {i}") for i in range(4)]
    projects.archive(all_projects[0].id)
    assert projects.list(limit=2, offset=1) == tuple(all_projects[2:4])
    assert len(projects.list(include_archived=True, limit=1)) == 1
    all_questions = [questions.create(all_projects[1].id, f"Question {i}") for i in range(4)]
    questions.archive(all_questions[0].id)
    assert questions.list_by_project(all_projects[1].id, limit=2, offset=1) == tuple(
        all_questions[2:4]
    )


@pytest.mark.parametrize("title", ["", " \n\t ", "x" * 301, None, 123])
def test_invalid_titles_cannot_be_persisted(projects, questions, title):
    project = projects.create("Valid")
    with pytest.raises(ValueError):
        projects.create(title)
    with pytest.raises(ValueError):
        questions.create(project.id, title)
    assert len(projects.list()) == 1
    assert questions.list_by_project(project.id) == ()


@pytest.mark.parametrize("identifier", [0, -1, True, "1"])
def test_invalid_identifiers_are_rejected(projects, questions, identifier):
    with pytest.raises(ValueError):
        projects.get(identifier)
    with pytest.raises(ValueError):
        questions.get(identifier)
    with pytest.raises(ValueError):
        questions.list_by_project(identifier)


@pytest.mark.parametrize("repository_name", ["projects", "questions"])
def test_mutations_report_missing_entities(request, repository_name):
    repository = request.getfixturevalue(repository_name)
    with pytest.raises(EntityNotFoundError):
        repository.update(9999, title="Does not exist")
    with pytest.raises(EntityNotFoundError):
        repository.archive(9999)


def test_validation_happens_before_any_update(projects, questions):
    project = projects.create("Original", "Keep this description")
    question = questions.create(project.id, "Original question", "Keep this too")
    with pytest.raises(ValueError):
        projects.update(project.id, title=" ", description="Must not be stored")
    with pytest.raises(ValueError):
        questions.update(question.id, title="Valid", description=123)
    assert projects.get(project.id) == project
    assert questions.get(question.id) == question


def test_empty_update_is_explicitly_rejected(projects, questions):
    with pytest.raises(ValueError):
        projects.update(1)
    with pytest.raises(ValueError):
        questions.update(1)


@pytest.mark.parametrize("limit,offset", [(0, 0), (1001, 0), (True, 0), (1, -1), (1, True)])
def test_invalid_pagination(projects, questions, limit, offset):
    with pytest.raises(ValueError):
        projects.list(limit=limit, offset=offset)
    with pytest.raises(ValueError):
        questions.list_by_project(1, limit=limit, offset=offset)
