"""Focused checkpoint/resume acceptance, temporary SQLite and synthetic providers only."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossDocumentError
from research_workbench.analysis.document_pipeline import Pricing
from research_workbench.analysis.stage_contracts import PROMPT_VERSIONS
from research_workbench.application.checkpoints import CheckpointUnavailable, inspect_execution
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.application.resume import service_for_resume, verify_resume
from research_workbench.persistence.checkpoint_models import checkpoints
from research_workbench.persistence.checkpoints import execution_digest
from research_workbench.persistence.document_models import stages
from research_workbench.persistence.document_repository import DocumentAnalysisRepository
from research_workbench.persistence.safe_migration import backup_and_upgrade


def failed(s, at=5):
    s.provider.invalid_at = at
    service = CrossDocumentAnalysisService(
        s.db,
        s.reader,
        s.provider,
        chunking=ChunkingConfig(100, 0),
        pricing=Pricing("CNY", "synthetic-v1", Decimal("1"), Decimal("2")),
    )
    with pytest.raises(CrossDocumentError):
        service.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    source = s.repo.list_by_question(s.question.id)[-1]
    s.provider.invalid_at = None
    s.provider.calls.clear()
    return source.id


def digest(s, rid):
    with s.db._session() as session:
        return execution_digest(session, rid)


@pytest.mark.parametrize("at", [1, 2, 3, 5, 7, 10])
def test_successful_resume_only_calls_remaining_stages_and_never_changes_history(cross_setup, at):
    s = cross_setup
    source_id = failed(s, at)
    before = digest(s, source_id)
    service, prepared, plan = service_for_resume(s.db, s.reader, s.provider, source_id)
    assert len(plan.results) == at - 1
    assert plan.new_calls == 11 - at
    assert not s.provider.calls
    run = service.execute_prepared(prepared, _resume=plan)
    assert run.id != source_id and run.status == "completed"
    assert len(s.provider.calls) == plan.new_calls
    assert digest(s, source_id) == before
    s.repo.get_history(run.id)  # Existing strict complete provenance-chain reader.
    calls = s.repo.get_call_usage(run.id)
    assert sum(c.call_outcome == "reused" for c in calls) == at - 1
    assert all(
        c.input_tokens is None and c.output_tokens is None
        for c in calls
        if c.call_outcome == "reused"
    )
    docs = DocumentAnalysisRepository(s.db)
    for member in s.repo.get_members(run.id):
        for row in docs.get_stages(member.document_execution_id):
            if (member.ordinal, row.ordinal) in {(m, n) for m, n, _ in plan.results}:
                assert row.response_id is None and row.usage_cost == "0"
                assert row.call_outcome == "reused"
    cost = s.repo.get_costs(run.id)
    assert cost.allocated_cost == plan.new_cost
    assert cost.usage_cost == Decimal("0.000180") * plan.new_calls
    assert s.repo.get_costs(source_id).known_usage_cost == Decimal("0.000180") * at
    assert plan.new_cost <= prepared.allocation.cost
    assert plan.new_context_units <= prepared.allocation.context_tokens
    with pytest.raises(CheckpointUnavailable):
        service_for_resume(s.db, s.reader, s.provider, source_id)


@pytest.mark.parametrize("change", ["source", "question", "model", "prompt", "contract", "budget"])
def test_changed_binding_refused_without_new_execution_or_call(cross_setup, change, monkeypatch):
    s = cross_setup
    source_id = failed(s)
    service, prepared, plan = service_for_resume(s.db, s.reader, s.provider, source_id)
    if change == "source":
        s.reader.texts["FAKE0001"] += " changed"
    elif change == "question":
        s.questions.update(s.question.id, description="Changed scope")
    elif change == "model":
        s.provider.model = "changed-model"
    elif change == "prompt":
        monkeypatch.setitem(PROMPT_VERSIONS, "map", "changed-prompt")
    elif change == "contract":
        from research_workbench.analysis.stage_contracts import CONTRACT_VERSIONS

        monkeypatch.setitem(CONTRACT_VERSIONS, "map", "changed-contract")
    elif change == "budget":
        prepared = replace(prepared, stage_budget=replace(prepared.stage_budget, output_tokens=1))
    with pytest.raises((CheckpointUnavailable, CrossDocumentError)):
        if change == "budget":
            verify_resume(service, source_id, prepared)
        else:
            service_for_resume(s.db, s.reader, s.provider, source_id)
    assert not s.provider.calls
    assert len(s.repo.list_by_question(s.question.id)) == 1


@pytest.mark.parametrize("damage", ["missing", "json", "hash", "projection", "request", "id"])
def test_incomplete_or_tampered_success_never_reassigned_ids(cross_setup, damage):
    s = cross_setup
    source_id = failed(s)
    with s.db._session() as session:
        predicate = (
            checkpoints.c.run_id == source_id,
            checkpoints.c.member == 1,
            checkpoints.c.ordinal == 1,
        )
        if damage == "missing":
            session.execute(delete(checkpoints).where(*predicate))
        elif damage in ("json", "hash", "projection", "request"):
            key = {
                "json": "output_json",
                "hash": "output_sha256",
                "projection": "projection_sha256",
                "request": "request_sha256",
            }[damage]
            session.execute(update(checkpoints).where(*predicate).values({key: "broken"}))
        else:
            member = s.repo.get_members(source_id)[0]
            row = (
                session.execute(
                    select(stages).where(
                        stages.c.execution_id == member.document_execution_id, stages.c.ordinal == 1
                    )
                )
                .mappings()
                .one()
            )
            value = json.loads(row.result_json)
            value["findings"][0]["finding_id"] = "f" * 64
            session.execute(
                update(stages)
                .where(stages.c.execution_id == member.document_execution_id, stages.c.ordinal == 1)
                .values(result_json=json.dumps(value))
            )
    with pytest.raises(CheckpointUnavailable):
        service_for_resume(s.db, s.reader, s.provider, source_id)
    assert not s.provider.calls


def test_duplicate_reviewed_start_is_atomic_and_old_executions_1_to_4_immutable(cross_setup):
    s = cross_setup
    ids = [failed(s) for _ in range(4)]
    snapshots = [digest(s, i) for i in ids]
    service, prepared, plan = service_for_resume(s.db, s.reader, s.provider, 4)
    second_service, second_prepared, second_plan = service_for_resume(s.db, s.reader, s.provider, 4)
    service.execute_prepared(prepared, _resume=plan)
    calls = len(s.provider.calls)
    with pytest.raises(CrossDocumentError):
        second_service.execute_prepared(second_prepared, _resume=second_plan)
    assert len(s.provider.calls) == calls
    assert [digest(s, i) for i in ids] == snapshots
    assert len(s.repo.list_by_question(s.question.id)) == 5


def test_failed_resume_can_itself_resume_without_double_charging(cross_setup):
    s = cross_setup
    source = failed(s)
    service, prepared, plan = service_for_resume(s.db, s.reader, s.provider, source)
    s.provider.invalid_at = 2
    with pytest.raises(CrossDocumentError):
        service.execute_prepared(prepared, _resume=plan)
    child = s.repo.list_by_question(s.question.id)[-1]
    assert s.repo.get_costs(child.id).known_usage_cost == Decimal("0.000360")
    s.provider.invalid_at = None
    s.provider.calls.clear()
    service, prepared, plan = service_for_resume(s.db, s.reader, s.provider, child.id)
    assert len(plan.results) == 5
    run = service.execute_prepared(prepared, _resume=plan)
    assert len(s.provider.calls) == 5
    assert s.repo.get_costs(run.id).usage_cost == Decimal("0.000900")
    ancestors = s.repo.get_ancestor_costs(run.id)
    assert [i for i, _ in ancestors] == [child.id, source]
    assert sum(c.known_usage_cost for _, c in ancestors) == Decimal("0.001260")


def test_backup_upgrade_preserves_all_historical_rows(cross_setup):
    import sqlite3

    s = cross_setup
    rid = failed(s)
    before = digest(s, rid)
    backup = backup_and_upgrade(s.db)
    assert backup.is_file() and digest(s, rid) == before
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT count(*) FROM cross_document_analysis_runs"
        ).fetchone() == (1,)
    assert inspect_execution(s.db, rid).checkpoint_resume_available


def test_0005_upgrade_keeps_legacy_successes_and_refuses_lossy_resume(cross_setup):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from research_workbench.persistence import database as database_module

    s = cross_setup
    rid = failed(s)
    config = Config()
    config.set_main_option(
        "script_location", str(Path(database_module.__file__).with_name("migrations"))
    )
    # Temporary DB only: remove the new tables to represent a genuine 0005 database.
    with s.db._engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0005")
    assert not inspect_execution(s.db, rid).checkpoint_resume_available
    old_rows = s.repo.get_call_usage(rid)
    backup = backup_and_upgrade(s.db)
    assert backup.is_file()
    assert old_rows == s.repo.get_call_usage(rid)
    assert not inspect_execution(s.db, rid).checkpoint_resume_available
    with pytest.raises(CheckpointUnavailable):
        service_for_resume(s.db, s.reader, s.provider, rid)
    assert not s.provider.calls
    with s.db._engine.begin() as connection:
        config.attributes["connection"] = connection
        command.check(config)


def test_checkpoint_preserves_json_order_and_exact_finding_ids(cross_setup, monkeypatch):
    from research_workbench.analysis.contracts import ProviderResult

    s = cross_setup
    original = s.provider.generate_stage

    def reordered(request):
        value = original(request)
        data = json.loads(value.output_json)
        for i, finding in enumerate(data["findings"]):
            data["findings"][i] = dict(reversed(list(finding.items())))
        raw = json.dumps(dict(reversed(list(data.items()))), ensure_ascii=False)
        return ProviderResult(raw, value.response_id, value.input_tokens, value.output_tokens)

    monkeypatch.setattr(s.provider, "generate_stage", reordered)
    source_id = failed(s, at=5)
    service, prepared, plan = service_for_resume(s.db, s.reader, s.provider, source_id)
    with s.db._session() as session:
        saved = list(
            session.execute(select(checkpoints).where(checkpoints.c.run_id == source_id)).mappings()
        )
    assert all(json.loads(cp.output_json).keys() == {"findings", "status"} for cp in saved)
    original_ids = [r.findings[0].finding_id for _, _, r in plan.results]
    run = service.execute_prepared(prepared, _resume=plan)
    with s.db._session() as session:
        copied = list(
            session.execute(
                select(checkpoints).where(
                    checkpoints.c.run_id == run.id, checkpoints.c.origin == "reuse"
                )
            ).mappings()
        )
    assert [r.output_json for r in saved] == [r.output_json for r in copied]
    assert len(original_ids) == 4


def test_valid_empty_successes_are_losslessly_reused(cross_setup):
    s = cross_setup
    s.provider.empty = set(range(1, 30))
    source_id = failed(s)
    service, prepared, plan = service_for_resume(s.db, s.reader, s.provider, source_id)
    assert len(plan.results) == 4 and all(not r.findings for _, _, r in plan.results)
    run = service.execute_prepared(prepared, _resume=plan)
    assert s.repo.get_history(run.id).status == "no_relevant_findings"
    assert len(s.provider.calls) == 6


def test_checkpoint_write_failure_rolls_back_success_with_no_incomplete_checkpoint(
    cross_setup, monkeypatch
):
    import research_workbench.persistence.cross_document_repository as module

    s = cross_setup

    def fail_save(*args, **kwargs):
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(module, "save_checkpoint", fail_save)
    service = CrossDocumentAnalysisService(s.db, s.reader, s.provider)
    with pytest.raises(CrossDocumentError):
        service.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    run = s.repo.list_by_question(s.question.id)[0]
    assert all(c.status != "completed" for c in s.repo.get_call_usage(run.id))
    with s.db._session() as session:
        assert not session.execute(select(checkpoints)).first()
