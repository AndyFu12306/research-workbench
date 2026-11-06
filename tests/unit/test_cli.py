"""CLI consent and persistence tests: synthetic content, no real API or library."""

import builtins
import getpass
import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_document_analysis import FakeStageProvider

from research_workbench import cli
from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.document_pipeline import RequestEstimate
from research_workbench.analysis.errors import AnalysisProviderFailure, AnalysisProviderIncomplete
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.integrations.ai.stages import prepare_stage_call
from research_workbench.integrations.zotero.models import ZoteroFulltext, ZoteroItem
from research_workbench.persistence import Database
from research_workbench.persistence.cross_document_repository import CrossDocumentRepository


@pytest.fixture
def session(monkeypatch, tmp_path):
    class Reader:
        reads = []
        fail = False

        def health_check(self):
            healthy = SimpleNamespace(status="healthy")
            return SimpleNamespace(status="healthy", client=healthy, local_api=healthy, cli=healthy)

        def search_items(self, query, limit):
            return (self.get_item("FAKE0001" if query == "first" else "FAKE0002"),)

        def get_item(self, key):
            return ZoteroItem(key, "Synthetic " + key, (), None, "journalArticle", None)

        def get_fulltext(self, key):
            self.reads.append(key)
            if self.fail:
                raise RuntimeError("PRIVATE fulltext payload")
            return ZoteroFulltext(key, "a" * 80 if key == "FAKE0001" else "b" * 90)

    reader, provider = Reader(), FakeStageProvider()
    provider.provider, provider.model = "deepseek", "synthetic-model"

    def estimate(request):
        _, result = prepare_stage_call(request, model=provider.model, provider="deepseek")
        return RequestEstimate(result.input_tokens, result.context_tokens)

    provider.estimate_stage = estimate
    config = {}

    @contextmanager
    def factory(*, environ):
        config.update(environ)
        yield provider

    monkeypatch.setattr(cli, "ZoteroClient", lambda: reader)
    monkeypatch.setattr(cli, "DeepSeekStageProvider", factory)
    monkeypatch.setattr(cli, "interactive_terminal", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", Mock(return_value="synthetic-test-secret"))
    return SimpleNamespace(
        reader=reader, provider=provider, config=config, path=tmp_path / "cli.db"
    )


def answers(monkeypatch, last="SEND", first="READ", cap="10"):
    values = iter(
        [
            first,
            "first",
            "1",
            "second",
            "1",
            "Project",
            "Question?",
            "Scope",
            "synthetic-model",
            "CNY",
            "1",
            "2",
            cap,
            last,
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda prompt: next(values))


def invoke(s, *args):
    return cli.main(["--database", str(s.path), *args])


def test_full_workflow_and_offline_history(session, monkeypatch, capsys):
    s = session
    answers(monkeypatch)
    assert invoke(s, "run") == 0
    assert s.reader.reads == ["FAKE0001", "FAKE0002"]
    assert [r.stage for r in s.provider.calls] == [
        "map",
        "document_reduce",
        "map",
        "document_reduce",
        "cross_document_reduce",
    ]
    output = capsys.readouterr().out
    assert "预计最多 5 次" in output and "执行 ID=1" in output
    assert "来源块" in output and "tokens=100/40" in output
    assert "输入操作单位上限=512000" in output
    assert "context 操作单位上限=1000000" in output
    assert "synthetic-test-secret" not in output
    assert "a" * 80 not in output and "b" * 90 not in output
    # No Zotero client or credential input is allowed when querying history.
    monkeypatch.setattr(cli, "ZoteroClient", Mock(side_effect=AssertionError))
    monkeypatch.setattr(cli.getpass, "getpass", Mock(side_effect=AssertionError))
    assert invoke(s, "history", "--question", "1") == 0
    assert invoke(s, "show", "1") == 0
    assert "completed" in capsys.readouterr().out
    with Database(s.path) as db:
        repo = CrossDocumentRepository(db)
        assert len(repo.get_call_usage(1)) == 5
        config = json.loads(repo.get(1).config_json)
        assert config["stage_budget"]["max_input_tokens"] == 512000
        assert config["budget"]["max_estimated_tokens"] == 8000000
        assert config["documents"][0]["planning_budget"]["max_input_tokens"] == 512000
    assert b"synthetic-test-secret" not in s.path.read_bytes()
    assert b"a" * 80 not in s.path.read_bytes()


@pytest.mark.parametrize("first,last", [("no", "SEND"), ("READ", "no")])
def test_cancel_never_calls_ai(session, monkeypatch, first, last):
    answers(monkeypatch, first=first, last=last)
    assert invoke(session, "run") == 2
    assert session.provider.calls == []
    if first != "READ":
        assert session.reader.reads == []
        cli.getpass.getpass.assert_not_called()
    with Database(session.path) as db:
        assert CrossDocumentRepository(db).list_by_question(1) == ()


@pytest.mark.parametrize(
    "code", ["provider_unavailable", "provider_timeout", "provider_sdk_incompatible"]
)
def test_provider_failure_safe_and_traceable(session, monkeypatch, capsys, code):
    answers(monkeypatch)
    session.provider.error_at = 1
    session.provider.error = AnalysisProviderFailure(code)
    assert invoke(session, "run") == 1
    text = capsys.readouterr().out
    assert code in text and "执行 ID=1" in text
    assert "synthetic-test-secret" not in text
    with Database(session.path) as db:
        repo = CrossDocumentRepository(db)
        assert repo.get(1).status != "completed"
        assert repo.get_call_usage(1)[-1].call_outcome == "not_started"


def test_fulltext_failure_never_calls_ai(session, monkeypatch, capsys):
    answers(monkeypatch)
    session.reader.fail = True
    assert invoke(session, "run") == 1
    assert session.provider.calls == []
    assert "PRIVATE" not in capsys.readouterr().out


def test_cli_cost_cap_refuses_before_send(session, monkeypatch, capsys):
    answers(monkeypatch, cap="0.000001")
    assert invoke(session, "run") == 1
    assert session.provider.calls == []
    assert "budget_exceeded" in capsys.readouterr().out


def test_unhealthy_local_api_stops_before_search_and_key(session, monkeypatch, capsys):
    answers(monkeypatch)
    health = session.reader.health_check()
    health.status, health.local_api.status = "degraded", "unavailable"
    monkeypatch.setattr(session.reader, "health_check", lambda: health)
    search = Mock(side_effect=AssertionError("Search must not start"))
    monkeypatch.setattr(session.reader, "search_items", search)
    assert invoke(session, "run") == 2
    search.assert_not_called()
    cli.getpass.getpass.assert_not_called()
    assert session.provider.calls == []


def test_duplicate_selection_refused(session, monkeypatch):
    values = iter(["READ", "first", "1", "first", "1"])
    monkeypatch.setattr(builtins, "input", lambda prompt: next(values))
    assert invoke(session, "run") == 2
    cli.getpass.getpass.assert_not_called()
    assert session.provider.calls == []


def test_key_hidden_input_must_not_fall_back_to_echo(session, monkeypatch):
    answers(monkeypatch)

    def warn(prompt):
        import warnings

        warnings.warn("No hidden terminal", getpass.GetPassWarning, stacklevel=2)
        pytest.fail("Echoing fallback must never execute")

    monkeypatch.setattr(cli.getpass, "getpass", warn)
    assert invoke(session, "run") == 2
    assert session.provider.calls == []


def test_noninteractive_refused_before_database_or_zotero(session, monkeypatch):
    monkeypatch.setattr(cli, "interactive_terminal", lambda: False)
    monkeypatch.setattr(cli, "ZoteroClient", Mock(side_effect=AssertionError))
    assert invoke(session, "run") == 2
    assert not session.path.exists()


def test_help_has_no_external_io(monkeypatch):
    monkeypatch.setattr(cli, "ZoteroClient", Mock(side_effect=AssertionError))
    with pytest.raises(SystemExit) as result:
        cli.main(["--help"])
    assert result.value.code == 0


def test_terminal_control_characters_removed(capsys):
    cli.display("Title\x1b[2J\u202e\rSAFE")
    text = capsys.readouterr().out
    assert "\x1b" not in text and "\u202e" not in text and "\r" not in text


def test_missing_history_does_not_create_database(session):
    assert invoke(session, "history", "--question", "1") == 2
    assert not session.path.exists()


def test_zotero_database_name_rejected(tmp_path):
    with pytest.raises(cli.CliError):
        cli.database_path(str(tmp_path / "zotero.sqlite"))


def test_prepared_snapshot_is_the_only_fulltext_read(cross_setup):
    s = cross_setup
    service = CrossDocumentAnalysisService(
        s.db, s.reader, s.provider, chunking=ChunkingConfig(100, 0)
    )
    prepared = service.prepare_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    assert s.provider.calls == [] and s.repo.list_by_question(s.question.id) == ()
    s.reader.texts = {}  # Execution must not reread changed Zotero input after confirmation.
    run = service.execute_prepared(prepared)
    assert run.status == "completed" and s.reader.reads == ["FAKE0001", "FAKE0002"]


def test_stale_question_rejected_after_preview(cross_setup):
    s = cross_setup
    service = CrossDocumentAnalysisService(s.db, s.reader, s.provider)
    prepared = service.prepare_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    s.questions.update(s.question.id, title="Changed after preview")
    with pytest.raises(CrossDocumentError):
        service.execute_prepared(prepared)
    assert s.provider.calls == [] and s.repo.list_by_question(s.question.id) == ()


def test_changed_budget_rejected_after_preview(cross_setup):
    s = cross_setup
    service = CrossDocumentAnalysisService(s.db, s.reader, s.provider)
    prepared = service.prepare_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    prepared = replace(prepared, budget=CrossBudget(max_calls=200))
    with pytest.raises(CrossDocumentError):
        service.execute_prepared(prepared)
    assert s.provider.calls == []


@pytest.mark.parametrize("reason", ["max_output_tokens", "content_filter", "PRIVATE_REASON", None])
def test_cli_incomplete_diagnostic_is_allowlisted(session, monkeypatch, capsys, reason):
    answers(monkeypatch)
    session.provider.error_at = 2
    session.provider.error = AnalysisProviderIncomplete(reason)
    assert invoke(session, "run") == 1
    text = capsys.readouterr().out
    expected = reason if reason in ("max_output_tokens", "content_filter") else "unknown"
    assert f"incomplete_details.reason={expected}" in text
    assert "PRIVATE_REASON" not in text and "synthetic-test-secret" not in text
    with Database(session.path) as db:
        repo = CrossDocumentRepository(db)
        assert repo.get(1).error_code == "provider_incomplete"
        failed = repo.get_call_usage(1)[1]
        assert failed.call_outcome == "response_received"
        assert failed.input_tokens is None and failed.output_tokens is None
    assert len(session.provider.calls) == 2


def test_cli_configured_output_budget_is_previewed_and_persisted(session, monkeypatch, capsys):
    answers(monkeypatch)
    assert (
        invoke(
            session,
            "run",
            "--stage-output-tokens",
            "8192",
            "--stage-context-tokens",
            "65536",
            "--stage-input-units",
            "24000",
        )
        == 0
    )
    assert all(r.budget.output_tokens == 8192 for r in session.provider.calls)
    text = capsys.readouterr().out
    assert "8192 tokens" in text and "context 操作单位上限=65536" in text
    with Database(session.path) as db:
        config = json.loads(CrossDocumentRepository(db).get(1).config_json)
        assert config["stage_budget"]["output_tokens"] == 8192
        assert config["documents"][0]["planning_budget"]["reserved_output_tokens"] == 8192


@pytest.mark.parametrize("output,context", [(8193, 65536), (8192, 32768), (0, 65536)])
def test_invalid_cli_output_budget_stops_before_io(session, output, context):
    assert (
        invoke(
            session,
            "run",
            "--stage-output-tokens",
            str(output),
            "--stage-context-tokens",
            str(context),
        )
        == 2
    )
    assert session.provider.calls == [] and session.reader.reads == []
    assert not session.path.exists()


def test_budget_refusal_before_confirmation(cross_setup):
    s = cross_setup
    service = CrossDocumentAnalysisService(
        s.db, s.reader, s.provider, cross_budget=CrossBudget(max_calls=4)
    )
    with pytest.raises(CrossDocumentError, match="allowance"):
        service.prepare_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    assert s.provider.calls == [] and s.repo.list_by_question(s.question.id) == ()
