"""Real temporary SQLite DML, including composite-key NULL and position attacks."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from research_workbench.persistence.cross_document_models import cross_inputs, members
from research_workbench.persistence.models import Base


def test_current_metadata_preserves_0005_foreign_keys(database):
    with database._engine.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar_one() == "0006"
        assert c.execute(text("pragma foreign_keys")).scalar_one() == 1
        assert c.execute(text("pragma foreign_key_check")).all() == []
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
        inspector = inspect(c)
        for table in (members, cross_inputs):
            for column in inspector.get_columns(table.name):
                if column["name"] not in ("child_request_sha256", "child_result_sha256"):
                    assert column["nullable"] is False
        fk = next(
            f
            for f in inspector.get_foreign_keys(cross_inputs.name)
            if f["name"] == "fk_cross_input_member"
        )
        assert fk["constrained_columns"] == [
            "run_id",
            "input_ordinal",
            "document_execution_id",
            "final_stage_ordinal",
        ]
        assert fk["referred_columns"] == [
            "run_id",
            "ordinal",
            "document_execution_id",
            "final_stage_ordinal",
        ]


@pytest.mark.parametrize("table", [members, cross_inputs])
def test_composite_keys_are_explicitly_not_null(table):
    assert all(c.nullable is False for c in table.primary_key.columns)


RELATIONS = [
    (members, c)
    for c in ("run_id", "ordinal", "source_id", "document_execution_id", "final_stage_ordinal")
]
RELATIONS += [
    (cross_inputs, c)
    for c in (
        "run_id",
        "parent_ordinal",
        "input_ordinal",
        "document_execution_id",
        "final_stage_ordinal",
    )
]


@pytest.mark.parametrize("table,column", RELATIONS)
@pytest.mark.parametrize("operation", ["insert", "update"])
def test_every_relationship_null_is_rejected_by_actual_dml(cross_setup, table, column, operation):
    from sqlalchemy import insert, select, update
    from sqlalchemy.exc import IntegrityError

    s = cross_setup
    s.repo.register(s.prepared)
    with s.db._engine.connect() as c:
        row = dict(c.execute(select(table)).mappings().first())
    row[column] = None
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(
            insert(table).values(**row)
            if operation == "insert"
            else update(table).values({column: None})
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"final_stage_ordinal": 4},
        {"final_stage_ordinal": 2},
        {"input_ordinal": 2},
        {"document_execution_id": 2, "final_stage_ordinal": 4},
        {"document_execution_id": 3, "final_stage_ordinal": 2},
        {"run_id": 999},
        {"parent_ordinal": 2},
        {"input_ordinal": 0},
        {"input_ordinal": 6},
        {"input_ordinal": 1.5},
        {"final_stage_ordinal": 1},
    ],
)
def test_exact_member_foreign_key_rejects_position_and_stage_mix(cross_setup, changes):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    s = cross_setup
    h = s.repo.register(s.prepared)
    assert [b.final_stage_ordinal for b in h.bindings] == [3, 4, 2]
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(
            update(cross_inputs)
            .where(cross_inputs.c.run_id == h.run_id, cross_inputs.c.input_ordinal == 1)
            .values(**changes)
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"child_request_sha256": "a" * 64},
        {"child_result_sha256": "b" * 64},
        {"child_request_sha256": "A" * 64, "child_result_sha256": "b" * 64},
        {"child_request_sha256": "bad", "child_result_sha256": "b" * 64},
    ],
)
def test_edge_hash_pair_constraints(cross_setup, changes):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    s = cross_setup
    s.repo.register(s.prepared)
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(update(cross_inputs).values(**changes))


def test_member_final_map_exists_but_four_column_binding_blocks_change(cross_setup):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    s = cross_setup
    h = s.repo.register(s.prepared)
    # A's ordinal 2 is a real Map, so the old-stage FK alone would accept it.
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(
            update(members)
            .where(members.c.run_id == h.run_id, members.c.ordinal == 1)
            .values(final_stage_ordinal=2)
        )


@pytest.mark.parametrize("target", ["parent", "member", "cross_stage", "document", "final_stage"])
def test_referenced_deletes_and_key_updates_are_restricted(cross_setup, target):
    from sqlalchemy import delete, update
    from sqlalchemy.exc import IntegrityError

    from research_workbench.persistence.cross_document_models import cross_stages, runs
    from research_workbench.persistence.document_models import executions, stages

    s = cross_setup
    s.repo.register(s.prepared)
    table = {
        "parent": runs,
        "member": members,
        "cross_stage": cross_stages,
        "document": executions,
        "final_stage": stages,
    }[target]
    for statement in (
        delete(table),
        update(table).values({list(table.primary_key.columns)[0].name: 999}),
    ):
        with pytest.raises(IntegrityError), s.db._engine.begin() as c:
            c.execute(statement)


def test_new_connections_have_foreign_keys_enabled(cross_setup):
    from research_workbench.persistence import Database

    s = cross_setup
    s.repo.register(s.prepared)
    with Database(s.db.path) as reopened, reopened._engine.connect() as c:
        assert c.execute(text("pragma foreign_keys")).scalar_one() == 1
        assert c.execute(text("pragma foreign_key_check")).all() == []


@pytest.mark.parametrize(
    "changes",
    [
        {"ordinal": 0},
        {"ordinal": 6},
        {"ordinal": 1.5},
        {"final_stage_ordinal": 102},
        {"identity_sha256": "A" * 64},
        {"identity_sha256": b"a" * 64},
        {"source_fingerprint": "bad"},
        {"content_sha256": "z" * 64},
        {"input_chars": 0},
        {"read_at": None},
    ],
)
def test_member_check_constraints_reject_actual_updates(cross_setup, changes):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    s = cross_setup
    h = s.repo.register(s.prepared)
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(
            update(members)
            .where(members.c.run_id == h.run_id, members.c.ordinal == 1)
            .values(**changes)
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "completed"},
        {"status": "running"},
        {"result_json": "{}"},
        {"call_outcome": "unknown"},
        {"request_sha256": "f" * 64},
        {"ordinal": 2},
    ],
)
def test_cross_stage_lifecycle_constraints(cross_setup, changes):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    from research_workbench.persistence.cross_document_models import cross_stages

    s = cross_setup
    s.repo.register(s.prepared)
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(update(cross_stages).values(**changes))


def test_upgrade_existing_0004_preserves_document_history_byte_for_byte(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import insert, select

    from research_workbench.persistence import (
        Database,
        ProjectRepository,
        QuestionRepository,
        QuestionSourceRepository,
    )
    from research_workbench.persistence.document_models import executions
    from research_workbench.persistence.models import utc_now

    path = tmp_path / "existing-0004.db"
    monkeypatch.setenv("WORKBENCH_DATABASE_PATH", str(path))
    config = Config("alembic.ini")
    command.upgrade(config, "0004")
    with Database(path) as db:
        q = QuestionRepository(db).create(ProjectRepository(db).create("Synthetic").id, "Question")
        s = QuestionSourceRepository(db).attach_reference(q.id, "FAKE0001").source
        with db._engine.begin() as c:
            c.execute(
                insert(executions).values(
                    question_id=q.id,
                    source_id=s.id,
                    status="completed",
                    provider="fake",
                    model="synthetic",
                    source_fingerprint="a" * 64,
                    content_sha256="b" * 64,
                    input_chars=100,
                    question_sha256="c" * 64,
                    chunk_count=1,
                    config_json='{"legacy":true}',
                    created_at=utc_now(),
                    completed_at=utc_now(),
                )
            )
        with db._engine.connect() as c:
            before = c.execute(select(executions)).all()
        db.upgrade()
        db.upgrade()
        with db._engine.connect() as c:
            assert c.execute(select(executions)).all() == before
            assert c.execute(text("pragma foreign_key_check")).all() == []
            assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
        command.check(config)


@pytest.mark.parametrize(
    "target,changes",
    [
        ("parent", {"question_id": 999}),
        ("member", {"run_id": 999}),
        ("member", {"source_id": 999}),
        ("member", {"document_execution_id": 999}),
        ("stage", {"run_id": 999}),
        ("edge", {"run_id": 999}),
        ("parent", {"write_revision": -1}),
        ("parent", {"document_count": 1}),
        ("parent", {"document_count": 6}),
    ],
)
def test_orphan_and_parent_bounds_actual_dml(cross_setup, target, changes):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    from research_workbench.persistence.cross_document_models import cross_stages, runs

    s = cross_setup
    s.repo.register(s.prepared)
    table = {"parent": runs, "member": members, "stage": cross_stages, "edge": cross_inputs}[target]
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(update(table).values(**changes))


def test_other_parent_execution_cannot_be_borrowed_at_same_input_position(cross_setup):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    s = cross_setup
    first, other = s.repo.register(s.prepared), s.repo.register(s.prepared)
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(
            update(cross_inputs)
            .where(cross_inputs.c.run_id == first.run_id, cross_inputs.c.input_ordinal == 1)
            .values(
                document_execution_id=other.bindings[0].document_execution_id,
                final_stage_ordinal=other.bindings[0].final_stage_ordinal,
            )
        )


@pytest.mark.parametrize(
    "violation",
    ["member_position", "member_source", "member_execution", "edge_position", "stage_position"],
)
def test_unique_constraints_with_other_keys_valid(cross_setup, violation):
    from sqlalchemy import insert, select
    from sqlalchemy.exc import IntegrityError

    from research_workbench.analysis.document_pipeline import ExecutionBudget
    from research_workbench.persistence.cross_document_models import cross_stages
    from research_workbench.persistence.document_repository import DocumentAnalysisRepository

    s = cross_setup
    h = s.repo.register(s.prepared)
    fresh_source = s.links.attach_reference(s.question.id, "FAKE0004").source
    p = s.prepared.documents[0]
    fresh = DocumentAnalysisRepository(s.db).create_run(
        s.question.id,
        s.sources[0].id,
        p.plan,
        provider="fake",
        model="synthetic",
        stage_budget=s.prepared.stage_budget,
        execution_budget=ExecutionBudget(),
        pricing=None,
        estimates=p.estimates,
    )
    table = (
        members
        if violation.startswith("member")
        else cross_inputs
        if violation == "edge_position"
        else cross_stages
    )
    with s.db._engine.connect() as c:
        row = dict(c.execute(select(table).where(table.c.run_id == h.run_id)).mappings().first())
    if violation.startswith("member"):
        row["ordinal"] = 1 if violation == "member_position" else 4
        row["source_id"] = s.sources[0].id if violation == "member_source" else fresh_source.id
        row["document_execution_id"] = (
            h.bindings[0].document_execution_id if violation == "member_execution" else fresh.id
        )
    with pytest.raises(IntegrityError), s.db._engine.begin() as c:
        c.execute(insert(table).values(**row))
