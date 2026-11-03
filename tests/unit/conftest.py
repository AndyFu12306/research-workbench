from unittest.mock import Mock

import pytest

from research_workbench.integrations.zotero import client, runner


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        Mock(side_effect=AssertionError("Unmocked subprocess in unit test")),
    )
    monkeypatch.setattr(
        client, "build_opener", Mock(side_effect=AssertionError("Unmocked network in unit test"))
    )


@pytest.fixture
def executable(tmp_path):
    path = tmp_path / "tool directory" / "zotero-cli.exe"
    path.parent.mkdir()
    path.write_bytes(b"unit-test-placeholder")
    path.chmod(0o755)
    return path.resolve()


@pytest.fixture
def database(tmp_path):
    from research_workbench.persistence import Database

    with Database(tmp_path / "test database #百分%.db") as database:
        database.upgrade()
        yield database


@pytest.fixture
def projects(database):
    from research_workbench.persistence import ProjectRepository

    return ProjectRepository(database)


@pytest.fixture
def questions(database):
    from research_workbench.persistence import QuestionRepository

    return QuestionRepository(database)


@pytest.fixture
def sources(database):
    from research_workbench.persistence import SourceRepository

    return SourceRepository(database)


@pytest.fixture
def links(database):
    from research_workbench.persistence import QuestionSourceRepository

    return QuestionSourceRepository(database)
