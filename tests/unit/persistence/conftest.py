import pytest

from research_workbench.persistence import Database, ProjectRepository, QuestionRepository


@pytest.fixture
def database(tmp_path):
    # Every test gets a fresh file; never use a configured development/user database.
    with Database(tmp_path / "test database #百分%.db") as database:
        database.upgrade()
        yield database


@pytest.fixture
def projects(database):
    return ProjectRepository(database)


@pytest.fixture
def questions(database):
    return QuestionRepository(database)
