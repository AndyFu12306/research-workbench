"""Atomic registration and ownership before any model call."""

from dataclasses import replace

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from research_workbench.analysis.cross_document_pipeline import CrossConflict
from research_workbench.persistence.cross_document_models import (
    cross_inputs,
    cross_stages,
    members,
    runs,
)
from research_workbench.persistence.document_models import chunks, executions, inputs, stages
from research_workbench.persistence.document_repository import DocumentAnalysisRepository
from research_workbench.persistence.errors import PersistenceError


def test_atomic_registration_all_plans_and_distinct_ordinals(cross_setup):
    s = cross_setup
    handle = s.repo.register(s.prepared)
    assert [b.final_stage_ordinal for b in handle.bindings] == [3, 4, 2]
    assert s.provider.calls == []
    with s.db._engine.connect() as c:
        assert len(c.execute(select(executions)).all()) == 3
        assert len(c.execute(select(chunks)).all()) == 6
        assert len(c.execute(select(stages)).all()) == 9
        assert len(c.execute(select(inputs)).all()) == 12
        assert len(c.execute(select(cross_inputs)).all()) == 3
    assert s.repo.get(handle.run_id).status == "running"


@pytest.mark.parametrize(
    "table", [runs, executions, chunks, stages, inputs, members, cross_stages, cross_inputs]
)
def test_t0_statement_failure_rolls_everything_back(cross_setup, table):
    s = cross_setup

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO " + table.name + " "):
            raise OperationalError("redacted", {}, Exception("private"))

    event.listen(s.db._engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(PersistenceError):
            s.repo.register(s.prepared)
    finally:
        event.remove(s.db._engine, "before_cursor_execute", fail)
    with s.db._engine.connect() as c:
        for t in (runs, executions, chunks, stages, inputs, members, cross_stages, cross_inputs):
            assert c.execute(select(t)).all() == []
    assert s.provider.calls == []


def test_owner_and_order_conflicts_cannot_claim_or_abort(cross_setup):
    s = cross_setup
    p = s.prepared
    h = s.repo.register(p)
    req = p.documents[0].requests[0]
    estimate = p.documents[0].estimates[0]
    with pytest.raises(CrossConflict):
        s.repo.start_document_stage(replace(h, owner="wrong"), p, 1, 1, req, estimate)
    with pytest.raises(CrossConflict):
        s.repo.start_document_stage(
            h, p, 2, 1, p.documents[1].requests[0], p.documents[1].estimates[0]
        )
    s.repo.start_document_stage(h, p, 1, 1, req, estimate)
    with pytest.raises(CrossConflict):
        s.repo.start_document_stage(h, p, 1, 1, req, estimate)
    with pytest.raises(CrossConflict):
        DocumentAnalysisRepository(s.db).fail_run(
            h.bindings[0].document_execution_id, "provider_failed"
        )
    assert s.repo.get(h.run_id).status == "running"
    assert s.provider.calls == []
