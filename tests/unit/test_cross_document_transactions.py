"""Actual SQLite sessions, commit failures and uncertain acknowledgements."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from test_cross_document_analysis import execute
from test_cross_document_gates import service

from research_workbench.analysis.cross_document_pipeline import CrossConflict, CrossDocumentError
from research_workbench.persistence import Database
from research_workbench.persistence.cross_document_models import cross_stages, runs
from research_workbench.persistence.cross_document_repository import CrossDocumentRepository
from research_workbench.persistence.document_repository import DocumentAnalysisRepository
from research_workbench.persistence.errors import PersistenceError


def test_two_sessions_same_claim_allow_at_most_one_model_call(cross_setup):
    s = cross_setup
    h = s.repo.register(s.prepared)
    p = s.prepared
    barrier = Barrier(2)

    def attempt():
        with Database(s.db.path) as database:
            repo = CrossDocumentRepository(database)
            barrier.wait(timeout=5)
            try:
                repo.start_document_stage(
                    h, p, 1, 1, p.documents[0].requests[0], p.documents[0].estimates[0]
                )
            except CrossConflict:
                return "conflict"
            s.provider.generate_stage(p.documents[0].requests[0])
            return "claimed"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["claimed", "conflict"]
    assert len(s.provider.calls) == 1
    assert s.repo.get(h.run_id).status == "running"
    assert (
        DocumentAnalysisRepository(s.db).get_stages(h.bindings[0].document_execution_id)[0].status
        == "running"
    )


def test_lock_timeout_is_conflict_and_never_cleans_up_winner(cross_setup):
    s = cross_setup
    h = s.repo.register(s.prepared)
    p = s.prepared
    with s.db._engine.begin() as c, Database(s.db.path, timeout=0.01) as other:
        c.execute(update(runs).where(runs.c.id == h.run_id).values(write_revision=1))
        with pytest.raises(CrossConflict):
            CrossDocumentRepository(other).start_document_stage(
                h, p, 1, 1, p.documents[0].requests[0], p.documents[0].estimates[0]
            )
    assert s.repo.get(h.run_id).status == "running" and s.provider.calls == []


@pytest.mark.parametrize(
    "target,expected_calls",
    [
        ("registration", 0),
        ("claim", 0),
        ("map_result", 1),
        ("document_result", 3),
        ("cross_result", 10),
    ],
)
def test_real_commit_failure_does_not_leave_partial_success(cross_setup, target, expected_calls):
    s = cross_setup
    state = {"armed": False, "failed": False}

    def statement(conn, cursor, sql, parameters, context, many):
        if state["failed"]:
            return
        values = context.compiled_parameters[0] if context.compiled_parameters else {}
        if target == "registration" and sql.startswith("INSERT INTO cross_document_stage_inputs"):
            state["armed"] = True
        if (
            target == "claim"
            and sql.startswith("UPDATE analysis_stage_runs")
            and values.get("status") == "running"
        ):
            state["armed"] = True
        if (
            target in ("map_result", "document_result")
            and sql.startswith("UPDATE analysis_stage_runs")
            and values.get("status") == "completed"
        ):
            state["armed"] = len(s.provider.calls) == (1 if target == "map_result" else 3)
        if (
            target == "cross_result"
            and sql.startswith("UPDATE cross_document_stage_runs")
            and values.get("status") == "completed"
        ):
            state["armed"] = True

    def commit(conn):
        if state["armed"] and not state["failed"]:
            state["failed"] = True
            raise OperationalError("redacted", {}, Exception("PRIVATE_DATABASE_SENTINEL"))

    event.listen(s.db._engine, "before_cursor_execute", statement)
    event.listen(s.db._engine, "commit", commit)
    try:
        with pytest.raises(CrossDocumentError) as e:
            execute(s)
        assert "PRIVATE" not in str(e.value)
    finally:
        event.remove(s.db._engine, "before_cursor_execute", statement)
        event.remove(s.db._engine, "commit", commit)
    assert state["failed"]
    assert len(s.provider.calls) == expected_calls
    history = s.repo.list_by_question(s.question.id)
    if target == "registration":
        assert history == ()
        return
    assert history[0].status == "failed"
    with s.db._engine.connect() as c:
        assert c.execute(select(cross_stages.c.result_json)).scalar_one() is None
    if target == "cross_result":
        repo = DocumentAnalysisRepository(s.db)
        assert all(
            repo.get(b.document_execution_id).status == "completed"
            for b in s.repo.get_members(history[0].id)
        )


def test_confirmed_t4_commit_is_read_back_without_second_call(cross_setup, monkeypatch):
    s = cross_setup
    svc = service(s)
    complete = svc._runs.complete_cross

    def uncertain(*args):
        complete(*args)
        raise PersistenceError("Acknowledgement unavailable")

    monkeypatch.setattr(svc._runs, "complete_cross", uncertain)
    run = svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert run.status == "completed" and len(s.provider.calls) == 10
    assert s.repo.get_history(run.id).status == "findings"


def test_conflict_loser_does_not_invoke_failure_cleanup(cross_setup, monkeypatch):
    s = cross_setup
    svc = service(s)

    def conflict(*args):
        raise CrossConflict()

    monkeypatch.setattr(svc._runs, "start_document_stage", conflict)
    monkeypatch.setattr(svc._runs, "abort", lambda *a, **k: pytest.fail("Loser cleaned up winner"))
    with pytest.raises(CrossConflict):
        svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert s.repo.list_by_question(s.question.id)[0].status == "running"
    assert s.provider.calls == []


def test_persistent_abort_commit_failure_leaves_running_without_partial_cleanup(cross_setup):
    s = cross_setup
    s.provider.invalid_at = 4
    state = {"aborting": False}

    def statement(conn, cursor, sql, params, context, many):
        values = context.compiled_parameters[0] if context.compiled_parameters else {}
        if sql.startswith("UPDATE analysis_stage_runs") and values.get("status") == "failed":
            state["aborting"] = True

    def commit(conn):
        if state["aborting"]:
            raise OperationalError("redacted", {}, Exception("PRIVATE_TF_FAILURE"))

    event.listen(s.db._engine, "before_cursor_execute", statement)
    event.listen(s.db._engine, "commit", commit)
    try:
        with pytest.raises(CrossDocumentError):
            execute(s)
    finally:
        event.remove(s.db._engine, "before_cursor_execute", statement)
        event.remove(s.db._engine, "commit", commit)
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == "running" and len(s.provider.calls) == 4
    repo = DocumentAnalysisRepository(s.db)
    bindings = s.repo.get_members(run.id)
    assert repo.get(bindings[0].document_execution_id).status == "completed"
    assert repo.get_stages(bindings[1].document_execution_id)[0].status == "running"
    assert all(r.status == "planned" for r in repo.get_stages(bindings[2].document_execution_id))


@pytest.mark.parametrize("after_response", [False, True])
def test_interrupt_between_stages_or_after_received_response_preserves_outcome(
    cross_setup, monkeypatch, after_response
):
    s = cross_setup
    svc = service(s)
    if after_response:

        def interrupt(*args):
            raise KeyboardInterrupt()

        monkeypatch.setattr(svc._runs, "complete_document_stage", interrupt)
    else:
        estimate = svc._documents._estimate

        def interrupt(request):
            if len(s.provider.calls) == 1:
                raise KeyboardInterrupt()
            return estimate(request)

        monkeypatch.setattr(svc._documents, "_estimate", interrupt)
    with pytest.raises(KeyboardInterrupt):
        svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    run = s.repo.list_by_question(s.question.id)[0]
    first = s.repo.get_members(run.id)[0]
    history = DocumentAnalysisRepository(s.db).get_stages(first.document_execution_id)
    assert run.status == "interrupted" and len(s.provider.calls) == 1
    assert history[0].call_outcome == "response_received"
    assert history[0].status == ("interrupted" if after_response else "completed")
    assert history[1].status == "not_started"


def test_parent_terminal_blocks_all_child_mutation_paths(cross_setup):
    s = cross_setup
    h = s.repo.register(s.prepared)
    s.repo.abort(h, "provider_failed")
    with pytest.raises(CrossConflict):
        s.repo.start_document_stage(
            h,
            s.prepared,
            1,
            1,
            s.prepared.documents[0].requests[0],
            s.prepared.documents[0].estimates[0],
        )
    with pytest.raises(CrossConflict):
        s.repo.abort(h, "interrupted", interrupted=True)
    assert s.repo.get(h.run_id).status == "failed"
