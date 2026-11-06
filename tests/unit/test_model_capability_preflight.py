"""Unregistered DeepSeek names must obey the same conservative output ceiling everywhere."""

import builtins
import json
from contextlib import contextmanager
from dataclasses import asdict
from unittest.mock import Mock

import pytest
from test_cli import session  # noqa: F401  (pytest fixture used via request.getfixturevalue)

from research_workbench import cli
from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.document_pipeline import RequestEstimate
from research_workbench.analysis.stage_contracts import StageBudget, StageCapabilityError
from research_workbench.application.checkpoints import inspect_execution
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.integrations.ai.stages import (
    DEEPSEEK_MODEL_LIMITS,
    model_output_limit,
    prepare_stage_call,
    validate_model_budget,
)

UNREGISTERED = "deepseek-unregistered-model"
CONSERVATIVE = 8192


def budget(output_tokens, context_tokens=1_000_000):
    return StageBudget(
        output_tokens=output_tokens,
        context_tokens=context_tokens,
        max_input_tokens=512_000,
    )


def answers_for(monkeypatch, model):
    values = iter(
        [
            "READ",
            "first",
            "1",
            "second",
            "1",
            "Project",
            "Question?",
            "Scope",
            model,
            "CNY",
            "1",
            "2",
            "10",
        ]
        + ["SEND"] * 120
    )
    monkeypatch.setattr(builtins, "input", lambda prompt: next(values))


def invoke(s, *args):
    return cli.main(["--database", str(s.path), *args])


def unregistered(request):
    s = request.getfixturevalue("session")
    s.provider.model = UNREGISTERED
    return s


@pytest.mark.parametrize("output", [1, 4096, CONSERVATIVE])
def test_unregistered_model_accepts_up_to_the_conservative_limit(output):
    validate_model_budget(UNREGISTERED, budget(output))
    assert model_output_limit(UNREGISTERED) == CONSERVATIVE


def test_unregistered_model_rejects_one_token_over_the_conservative_limit():
    with pytest.raises(StageCapabilityError):
        validate_model_budget(UNREGISTERED, budget(CONSERVATIVE + 1))


@pytest.mark.parametrize("model", sorted(DEEPSEEK_MODEL_LIMITS))
def test_registered_model_keeps_its_verified_capacity(model):
    context_limit, output_limit = DEEPSEEK_MODEL_LIMITS[model]
    validate_model_budget(model, budget(output_limit, context_limit))
    with pytest.raises(StageCapabilityError):
        validate_model_budget(model, budget(output_limit + 1, context_limit))
    with pytest.raises(StageCapabilityError):
        validate_model_budget(model, budget(output_limit, context_limit + 1))


def test_unregistered_defaults_are_rejected_before_key_or_paid_call(request, monkeypatch, capsys):
    s = unregistered(request)
    answers_for(monkeypatch, UNREGISTERED)
    assert invoke(s, "run") == 2
    assert s.provider.calls == []
    assert s.reader.reads == []
    cli.getpass.getpass.assert_not_called()
    assert "未登记模型" in capsys.readouterr().out


def test_unregistered_all_stages_at_the_limit_still_run(request, monkeypatch, capsys):
    s = unregistered(request)
    answers_for(monkeypatch, UNREGISTERED)
    assert invoke(s, "run", "--stage-output-tokens", str(CONSERVATIVE)) == 0
    assert len(s.provider.calls) == 5
    assert all(call.budget.output_tokens == CONSERVATIVE for call in s.provider.calls)
    assert "执行 ID=1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "flag",
    ["--map-output-tokens", "--document-reduce-output-tokens", "--cross-reduce-output-tokens"],
)
def test_each_stage_budget_is_checked_before_any_paid_call(request, monkeypatch, capsys, flag):
    s = unregistered(request)
    answers_for(monkeypatch, UNREGISTERED)
    args = [
        "--map-output-tokens",
        str(CONSERVATIVE),
        "--document-reduce-output-tokens",
        str(CONSERVATIVE),
        "--cross-reduce-output-tokens",
        str(CONSERVATIVE),
        flag,
        str(CONSERVATIVE + 1),
    ]
    assert invoke(s, "run", *args) == 2
    assert s.provider.calls == []
    cli.getpass.getpass.assert_not_called()


def historical_config(stage, document_reduce=None, cross_reduce=None):
    config = {"stage_budget": asdict(stage)}
    if document_reduce is not None or cross_reduce is not None:
        config["reduce_stage_budgets"] = {
            "document_reduce": asdict(document_reduce or stage),
            "cross_document_reduce": asdict(cross_reduce or stage),
        }
    return json.dumps(config)


def test_history_preflight_rejects_unregistered_over_limit_budget():
    with pytest.raises(cli.CliError):
        cli.require_history_model_budgets(
            UNREGISTERED,
            historical_config(budget(CONSERVATIVE), budget(32768), budget(32768)),
        )


def test_history_preflight_rejects_unregistered_uniform_over_limit_budget():
    with pytest.raises(cli.CliError):
        cli.require_history_model_budgets(UNREGISTERED, historical_config(budget(32768)))


def test_history_preflight_accepts_legal_unregistered_budgets():
    cli.require_history_model_budgets(UNREGISTERED, historical_config(budget(CONSERVATIVE)))
    cli.require_history_model_budgets(
        UNREGISTERED,
        historical_config(budget(CONSERVATIVE), budget(CONSERVATIVE), budget(CONSERVATIVE)),
    )


def test_history_preflight_keeps_registered_model_capacity():
    cli.require_history_model_budgets(
        "deepseek-flash", historical_config(budget(32768), budget(32768), budget(32768))
    )
    with pytest.raises(cli.CliError):
        cli.require_history_model_budgets("deepseek-flash", historical_config(budget(384001)))


def failed_run(s, budgets, *, fail_at_last=False):
    s.provider.provider = "deepseek"
    s.provider.model = UNREGISTERED
    s.provider.calls.clear()

    def estimate(request):
        _, result = prepare_stage_call(request, model=s.provider.model, provider="deepseek")
        return RequestEstimate(result.input_tokens, result.context_tokens)

    s.provider.estimate_stage = estimate
    map_budget, document_budget, cross_budget = budgets
    service = CrossDocumentAnalysisService(
        s.db,
        s.reader,
        s.provider,
        chunking=ChunkingConfig(100, 0),
        stage_budget=map_budget,
        document_reduce_budget=document_budget,
        cross_reduce_budget=cross_budget,
        cross_budget=CrossBudget(max_estimated_tokens=8_000_000),
    )
    selected = tuple(source.id for source in s.sources[:2])
    if fail_at_last:
        prepared = service.prepare_analysis(s.question.id, selected)
        s.provider.invalid_at = prepared.allocation.calls
        with pytest.raises(CrossDocumentError):
            service.execute_prepared(prepared)
    else:
        with pytest.raises(CrossDocumentError):
            service.run_analysis(s.question.id, selected)
    run = s.repo.list_by_question(s.question.id)[-1]
    s.provider.invalid_at = None
    s.provider.calls.clear()
    return run.id


def test_resume_rejects_historical_illegal_budget_before_zotero_key_or_paid_call(
    cross_setup, monkeypatch, capsys
):
    s = cross_setup
    run_id = failed_run(s, (budget(CONSERVATIVE), budget(32768), budget(32768)))
    assert inspect_execution(s.db, run_id).checkpoint_resume_available
    reads = len(s.reader.reads)
    monkeypatch.setattr(cli, "ZoteroClient", Mock(side_effect=AssertionError("no Zotero")))
    monkeypatch.setattr(cli.getpass, "getpass", Mock(side_effect=AssertionError("no key")))
    monkeypatch.setattr(cli, "interactive_terminal", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda prompt: UNREGISTERED)
    assert cli.main(["--database", str(s.db.path), "resume", str(run_id)]) == 2
    assert s.provider.calls == [] and len(s.reader.reads) == reads
    assert "未登记模型" in capsys.readouterr().out


def test_resume_accepts_legal_historical_budget_and_reuses_without_rebilling(
    cross_setup, monkeypatch, capsys
):
    s = cross_setup
    legal = budget(CONSERVATIVE)
    run_id = failed_run(s, (legal, legal, legal), fail_at_last=True)
    assert inspect_execution(s.db, run_id).checkpoint_resume_available
    before_run, before_usage = s.repo.get(run_id), s.repo.get_call_usage(run_id)
    reads = len(s.reader.reads)

    @contextmanager
    def provider_factory(*, environ):
        yield s.provider

    monkeypatch.setattr(cli, "ZoteroClient", lambda: s.reader)
    monkeypatch.setattr(cli, "DeepSeekStageProvider", provider_factory)
    monkeypatch.setattr(cli, "interactive_terminal", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", Mock(return_value="synthetic-key"))
    values = iter([UNREGISTERED, "READ", "SEND", "SEND"])
    monkeypatch.setattr(builtins, "input", lambda prompt: next(values))
    assert cli.main(["--database", str(s.db.path), "resume", str(run_id)]) == 0
    assert len(s.provider.calls) == 1
    assert len(s.reader.reads) == reads + 2
    assert s.repo.get(run_id) == before_run
    assert s.repo.get_call_usage(run_id) == before_usage
    assert "执行 ID=" in capsys.readouterr().out
