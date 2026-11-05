"""Real temporary SQLite DML, including composite-key NULL and position attacks."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from research_workbench.persistence.cross_document_models import cross_inputs, members
from research_workbench.persistence.models import Base


def test_0005_metadata_and_foreign_key_definitions(database):
    with database._engine.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar_one() == "0005"
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
