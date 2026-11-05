"""Additive 0004 migration and actual SQLite relationship/lifecycle constraints."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from research_workbench.persistence import Database, ProjectRepository, QuestionRepository
from research_workbench.persistence.document_models import chunks, executions, inputs, stages
from research_workbench.persistence.models import Base, utc_now
from research_workbench.persistence.source_repositories import QuestionSourceRepository


def test_upgrade_0003_preserves_existing_completed_analysis(tmp_path, monkeypatch):
    path = tmp_path / "existing #中文%.db"
    monkeypatch.setenv("WORKBENCH_DATABASE_PATH", str(path))
    config = Config("alembic.ini")
    command.upgrade(config, "0003")
    with Database(path) as db:
        q = QuestionRepository(db).create(ProjectRepository(db).create("Synthetic").id, "Question")
        link = QuestionSourceRepository(db).attach_reference(q.id, "FAKE0001")
        with db._engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO analysis_runs (question_id,status,provider,model,prompt_version,"
                    "schema_version,result_json,completed_at) "
                    "VALUES (:q,'completed','fake','fake','v1','v1',"
                    "'existing-generated-result',CURRENT_TIMESTAMP)"
                ),
                {"q": q.id},
            )
        db.upgrade()
        db.upgrade()
        with db._engine.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0005"
            assert (
                c.execute(text("SELECT result_json FROM analysis_runs")).scalar_one()
                == "existing-generated-result"
            )
            assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
        assert QuestionSourceRepository(db).list_for_question(q.id) == (link,)
    command.check(config)


@pytest.fixture
def plan_rows(database, projects, questions, links):
    q = questions.create(projects.create("Synthetic").id, "Question")
    link = links.attach_reference(q.id, "FAKE0001")
    ids = []
    with database._engine.begin() as c:
        for _ in range(2):
            eid = c.execute(
                insert(executions).values(
                    question_id=q.id,
                    source_id=link.source_id,
                    status="running",
                    provider="fake",
                    model="synthetic",
                    source_fingerprint="a" * 64,
                    content_sha256="b" * 64,
                    question_sha256="c" * 64,
                    input_chars=10,
                    chunk_count=1,
                    config_json="{}",
                    created_at=utc_now(),
                )
            ).inserted_primary_key[0]
            ids.append(eid)
            c.execute(
                insert(chunks).values(
                    execution_id=eid,
                    ordinal=1,
                    chunk_id="d" * 64,
                    start=0,
                    end=10,
                    source_fingerprint="a" * 64,
                    content_sha256="b" * 64,
                )
            )
            for ordinal, stage in ((1, "map"), (2, "document_reduce")):
                c.execute(
                    insert(stages).values(
                        execution_id=eid,
                        ordinal=ordinal,
                        stage=stage,
                        status="planned",
                        prompt_version="synthetic",
                        contract_version="synthetic",
                        call_outcome="not_started",
                        created_at=utc_now(),
                    )
                )
    return database, ids


@pytest.mark.parametrize(
    "change", ["orphan_execution", "orphan_child", "cycle", "both_inputs", "neither_input"]
)
def test_stage_input_real_fk_and_shape_constraints(plan_rows, change):
    db, ids = plan_rows
    values = dict(execution_id=ids[0], parent_ordinal=2, ordinal=1, child_ordinal=1)
    if change == "orphan_execution":
        values["execution_id"] = 999
    elif change == "orphan_child":
        values["child_ordinal"] = 0
    elif change == "cycle":
        values["child_ordinal"] = 2
    elif change == "both_inputs":
        values["chunk_ordinal"] = 1
    else:
        values["child_ordinal"] = None
    with pytest.raises(IntegrityError), db._engine.begin() as c:
        c.execute(insert(inputs).values(**values))


def test_duplicate_chunk_and_stage_inputs_are_rejected(plan_rows):
    db, ids = plan_rows
    with pytest.raises(IntegrityError), db._engine.begin() as c:
        c.execute(
            insert(chunks).values(
                execution_id=ids[0],
                ordinal=2,
                chunk_id="d" * 64,
                start=5,
                end=10,
                source_fingerprint="a" * 64,
                content_sha256="b" * 64,
            )
        )
    with db._engine.begin() as c:
        c.execute(
            insert(inputs).values(execution_id=ids[0], parent_ordinal=2, ordinal=1, child_ordinal=1)
        )
    with pytest.raises(IntegrityError), db._engine.begin() as c:
        c.execute(
            insert(inputs).values(execution_id=ids[0], parent_ordinal=2, ordinal=2, child_ordinal=1)
        )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE analysis_stage_runs SET status='completed',completed_at=CURRENT_TIMESTAMP",
        "UPDATE analysis_stage_runs SET input_tokens=-1",
        "UPDATE analysis_stage_runs SET call_outcome='success-guessed'",
        "UPDATE document_analysis_runs SET status='completed'",
        "UPDATE document_analysis_runs SET content_sha256='not-a-hash'",
        "UPDATE document_analysis_chunks SET start=10,end=1",
    ],
)
def test_history_constraints_reject_invalid_direct_sql(plan_rows, sql):
    db, _ = plan_rows
    with pytest.raises(IntegrityError), db._engine.begin() as c:
        c.execute(text(sql))


def test_stage_edges_cannot_borrow_child_from_another_execution(plan_rows):
    db, ids = plan_rows
    # Only execution B has ordinal 3; execution A cannot point at it.
    with db._engine.begin() as c:
        c.execute(
            insert(stages).values(
                execution_id=ids[1],
                ordinal=3,
                stage="map",
                status="planned",
                prompt_version="synthetic",
                contract_version="synthetic",
                call_outcome="not_started",
            )
        )
        c.execute(
            insert(stages).values(
                execution_id=ids[0],
                ordinal=4,
                stage="document_reduce",
                status="planned",
                prompt_version="synthetic",
                contract_version="synthetic",
                call_outcome="not_started",
            )
        )
    with pytest.raises(IntegrityError), db._engine.begin() as c:
        c.execute(
            insert(inputs).values(execution_id=ids[0], parent_ordinal=4, ordinal=1, child_ordinal=3)
        )
