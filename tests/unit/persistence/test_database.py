from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from research_workbench.persistence import (
    Database,
    MigrationError,
    PersistenceError,
    ProjectRepository,
)
from research_workbench.persistence.config import resolve_database_path
from research_workbench.persistence.models import ProjectRow


def test_path_priority_and_default_are_not_user_specific(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_database_path(environ={}) == tmp_path / "data" / "research_workbench.db"
    env = {"WORKBENCH_DATABASE_PATH": "configured/workbench.db"}
    assert resolve_database_path(environ=env) == tmp_path / "configured" / "workbench.db"
    assert resolve_database_path(tmp_path / "override.db", environ=env) == tmp_path / "override.db"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("path", ["", ":memory:", "sqlite:///other.db"])
def test_invalid_database_configuration(path):
    with pytest.raises(ValueError):
        resolve_database_path(path)


def test_directory_is_not_a_database_file(tmp_path):
    with pytest.raises(ValueError):
        resolve_database_path(tmp_path)


def test_no_implicit_schema_creation(tmp_path):
    path = tmp_path / "new" / "workbench.db"
    with Database(path) as database:
        assert not path.exists()
        with pytest.raises(PersistenceError, match="Database.upgrade"):
            ProjectRepository(database).list()
        assert inspect(database._engine).get_table_names() == []
        database.upgrade()
        assert ProjectRepository(database).list() == ()


def test_foreign_keys_are_enabled_on_new_connections(database):
    for _ in range(2):
        with database._engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        database._engine.dispose()


def test_database_rejects_orphan_question(database):
    with pytest.raises(IntegrityError), database._engine.begin() as connection:
        connection.execute(
            text("INSERT INTO questions (project_id, title) VALUES (9999, 'Orphan')")
        )


def test_database_prevents_parent_deletion(database, projects, questions):
    project = projects.create("Keep parent")
    question = questions.create(project.id, "Keep child")
    with pytest.raises(IntegrityError), database._engine.begin() as connection:
        connection.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project.id})
    assert projects.get(project.id) == project
    assert questions.get(question.id) == question


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("projects", "title", ""),
        ("projects", "status", "deleted"),
        ("questions", "title", ""),
        ("questions", "status", "unsupported"),
    ],
)
def test_database_enforces_constraints_beyond_repository(database, projects, table, field, value):
    parent = projects.create("Parent")
    # Only constant identifiers from the parameter set; values remain bound parameters.
    columns = "title, status" if table == "projects" else "project_id, title, status"
    values = ":title, :status" if table == "projects" else ":parent, :title, :status"
    params = {"parent": parent.id, "title": "Valid", "status": "active", field: value}
    with pytest.raises(IntegrityError), database._engine.begin() as connection:
        connection.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({values})"), params)


def test_failed_transaction_rolls_back_all_rows(database, projects):
    with pytest.raises(RuntimeError, match="Simulated"), database._session() as session:
        session.add(ProjectRow(title="Should roll back"))
        session.flush()
        raise RuntimeError("Simulated failure after flush")
    assert projects.list() == ()
    assert projects.create("Next operation still works").title == "Next operation still works"


def test_sqlalchemy_failure_is_wrapped_and_next_session_recovers(database, projects):
    with pytest.raises(PersistenceError) as error, database._session() as session:
        session.add(ProjectRow(title="Invalid status", status="invalid"))
        session.flush()
    assert isinstance(error.value.__cause__, IntegrityError)
    assert "INSERT" not in str(error.value)
    assert projects.list() == ()


def test_timestamps_normalize_to_utc(database, projects):
    instant = datetime(2026, 1, 2, 8, 30, tzinfo=timezone(timedelta(hours=8)))
    with database._session() as session:
        row = ProjectRow(title="Timezone test", created_at=instant, updated_at=instant)
        session.add(row)
        session.flush()
        row_id = row.id
    saved = projects.get(row_id)
    assert saved.created_at == datetime(2026, 1, 2, 0, 30, tzinfo=UTC)
    assert saved.created_at.tzinfo is UTC


def test_different_database_instances_are_isolated(tmp_path):
    with Database(tmp_path / "a.db") as first, Database(tmp_path / "b.db") as second:
        first.upgrade()
        second.upgrade()
        ProjectRepository(first).create("Only in the first database")
        assert ProjectRepository(second).list() == ()


def test_migration_refuses_an_unrelated_existing_database(tmp_path):
    with Database(tmp_path / "unrelated.db") as database:
        with database._engine.begin() as connection:
            connection.execute(text("CREATE TABLE unrelated_data (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO unrelated_data (id) VALUES (1)"))
        with pytest.raises(MigrationError, match="unversioned"):
            database.upgrade()
        assert inspect(database._engine).get_table_names() == ["unrelated_data"]
        with database._engine.connect() as connection:
            assert connection.execute(text("SELECT id FROM unrelated_data")).scalar_one() == 1


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_database_busy_timeout_is_finite(tmp_path, timeout):
    with pytest.raises(ValueError):
        Database(tmp_path / "unused.db", timeout=timeout)
