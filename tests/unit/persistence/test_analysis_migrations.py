import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from research_workbench.persistence import (
    Database,
    ProjectRepository,
    QuestionRepository,
    QuestionSourceRepository,
)
from research_workbench.persistence.models import Base


@pytest.mark.parametrize("initial", ["0001", "0002"])
def test_existing_research_and_sources_survive_analysis_upgrade(tmp_path, monkeypatch, initial):
    path = tmp_path / "existing research #百分%.db"
    monkeypatch.setenv("WORKBENCH_DATABASE_PATH", str(path))
    config = Config("alembic.ini")
    command.upgrade(config, initial)
    with Database(path) as db:
        project = ProjectRepository(db).create("Existing research")
        question = QuestionRepository(db).create(project.id, "Existing question")
        command.upgrade(config, "0002")
        link = QuestionSourceRepository(db).attach_reference(question.id, "FAKE0001")
        db.upgrade()
        db.upgrade()
        assert ProjectRepository(db).get(project.id) == project
        assert QuestionRepository(db).get(question.id) == question
        assert QuestionSourceRepository(db).list_for_question(question.id) == (link,)
        with db._engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0004"
            )
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        inspector = inspect(db._engine)
        assert len(inspector.get_foreign_keys("analysis_source_inputs")) == 2
        with db._engine.begin() as connection:
            analysis_id = connection.execute(
                text(
                    "INSERT INTO analysis_runs "
                    "(question_id,status,provider,model,prompt_version,schema_version) "
                    "VALUES (:q,'running','fake','test','v1','v1') RETURNING id"
                ),
                {"q": question.id},
            ).scalar_one()
        for bad_id in ("analysis", "source"):
            with pytest.raises(IntegrityError), db._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO analysis_source_inputs "
                        "(analysis_id,source_id,ordinal,input_kind,content_sha256,input_chars) "
                        "VALUES (:a,:s,1,'abstract',:h,10)"
                    ),
                    {
                        "a": 9999 if bad_id == "analysis" else analysis_id,
                        "s": link.source_id if bad_id == "analysis" else 9999,
                        "h": "a" * 64,
                    },
                )
