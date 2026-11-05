import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from research_workbench.persistence import Database, ProjectRepository, QuestionRepository
from research_workbench.persistence.models import Base


@pytest.mark.parametrize("filename", ["first.db", "another empty database #百分%.db"])
def test_upgrade_head_from_fresh_database(tmp_path, monkeypatch, filename):
    path = tmp_path / filename
    monkeypatch.setenv("WORKBENCH_DATABASE_PATH", str(path))
    # This is the Alembic command API used by `alembic upgrade head`, including the CLI env path.
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    with Database(path) as database:
        inspector = inspect(database._engine)
        assert set(inspector.get_table_names()) == {
            "alembic_version",
            "projects",
            "questions",
            "sources",
            "question_sources",
            "analysis_runs",
            "analysis_source_inputs",
            "document_analysis_runs",
            "document_analysis_chunks",
            "analysis_stage_runs",
            "analysis_stage_inputs",
        }
        assert {c["name"] for c in inspector.get_columns("projects")} == {
            "id",
            "title",
            "description",
            "status",
            "created_at",
            "updated_at",
        }
        assert {c["name"] for c in inspector.get_columns("questions")} == {
            "id",
            "project_id",
            "title",
            "description",
            "status",
            "created_at",
            "updated_at",
        }
        fk = inspector.get_foreign_keys("questions")
        assert len(fk) == 1 and fk[0]["constrained_columns"] == ["project_id"]
        assert fk[0]["referred_table"] == "projects" and fk[0]["referred_columns"] == ["id"]
        assert fk[0]["options"]["ondelete"] == "RESTRICT"
        assert any(i["column_names"] == ["project_id"] for i in inspector.get_indexes("questions"))
        with database._engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0004"
            )
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        project = ProjectRepository(database).create("Created in migrated database")
        assert QuestionRepository(database).create(project.id, "Question").project_id == project.id


def test_repeated_upgrade_is_idempotent_and_preserves_data(database, projects, questions):
    project = projects.create("Existing project")
    question = questions.create(project.id, "Existing question")
    database.upgrade()
    database.upgrade()
    assert projects.get(project.id) == project
    assert questions.get(question.id) == question
