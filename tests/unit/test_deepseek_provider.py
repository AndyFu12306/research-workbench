import copy
import hashlib
import json
import logging

import httpx
import pytest
from analysis_fakes import FakeZoteroClient, valid_output
from deepseek_validation_support import consented_zotero_item, require_deepseek_config

from research_workbench.analysis.contracts import INSTRUCTIONS, AnalysisRequest, CorpusSource
from research_workbench.analysis.corpus import ResearchCorpusBuilder
from research_workbench.analysis.errors import (
    AnalysisConfigurationError,
    AnalysisOutputValidationError,
    AnalysisProviderFailure,
    AnalysisStateError,
)
from research_workbench.analysis.schema import OUTPUT_SCHEMA
from research_workbench.application.analysis import AnalysisService
from research_workbench.integrations.ai import deepseek as adapter
from research_workbench.persistence.analysis_repository import AnalysisRepository

ENV = {"DEEPSEEK_API_KEY": "synthetic-secret", "DEEPSEEK_MODEL": "configured-model"}


@pytest.fixture
def wire(monkeypatch):
    state = {
        "calls": [],
        "status": 200,
        "error": None,
        "body": {
            "id": "00000000-0000-0000-0000-000000000001",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "configured-model",
            "output": [
                {"type": "reasoning", "id": "reasoning_fake", "summary": []},
                {
                    "type": "message",
                    "id": "message_fake",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(valid_output()),
                            "annotations": [],
                        }
                    ],
                },
            ],
            "usage": {"input_tokens": 41, "output_tokens": 23, "total_tokens": 64},
        },
    }

    def transport(request):
        state["calls"].append(json.loads(request.content))
        assert str(request.url) == "https://api.deepseek.com/responses"
        assert request.headers["Authorization"] == "Bearer synthetic-secret"
        assert not request.headers.get("OpenAI-Organization")
        assert not request.headers.get("OpenAI-Project")
        if state["error"]:
            raise state["error"]("synthetic secret should not leak", request=request)
        return httpx.Response(state["status"], json=state["body"])

    original = adapter.openai.OpenAI

    def factory(**kwargs):
        assert kwargs["base_url"] == "https://api.deepseek.com"
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 60
        return original(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport))
        )

    monkeypatch.setattr(adapter.openai, "OpenAI", factory)
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-provider-secret")
    monkeypatch.setenv("OPENAI_ORG_ID", "private-org-do-not-inherit")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "private-project-do-not-inherit")
    return state


def request():
    return AnalysisRequest(
        "synthetic question",
        (CorpusSource(1, "S1", "abstract", "PRIVATE_SOURCE_SENTINEL synthetic input"),),
    )


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"DEEPSEEK_API_KEY": "synthetic-secret"},
        {"DEEPSEEK_MODEL": "configured-model"},
        {**ENV, "DEEPSEEK_API_KEY": " "},
        {**ENV, "DEEPSEEK_MODEL": " "},
        {**ENV, "DEEPSEEK_TIMEOUT_SECONDS": "nan"},
        {**ENV, "DEEPSEEK_TIMEOUT_SECONDS": "0"},
    ],
)
def test_explicit_configuration_required(env):
    with pytest.raises(AnalysisConfigurationError):
        adapter.DeepSeekAnalysisProvider(environ=env)


def test_responses_request_normalization_and_no_payload_logging(wire, caplog):
    caplog.set_level(logging.DEBUG)
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        result = provider.generate(request())
        assert provider.provider == "deepseek" and provider.model == ENV["DEEPSEEK_MODEL"]
    assert result.input_tokens == 41 and result.output_tokens == 23
    assert result.response_id == wire["body"]["id"]
    assert json.loads(result.output_json) == valid_output()
    assert len(wire["calls"]) == 1
    sent = wire["calls"][0]
    assert sent["model"] == ENV["DEEPSEEK_MODEL"] and sent["instructions"] == INSTRUCTIONS
    assert sent["text"]["format"] == {
        "type": "json_schema",
        "name": "research_analysis",
        "schema": OUTPUT_SCHEMA,
        "strict": True,
    }
    assert sent["max_output_tokens"] == 4096
    assert "store" not in sent and "truncation" not in sent and "tools" not in sent
    assert "PRIVATE_SOURCE_SENTINEL" not in caplog.text and "synthetic-secret" not in caplog.text


@pytest.mark.parametrize("missing", ["all", "usage_output", "id"])
def test_optional_metadata_is_not_invented(wire, missing):
    if missing == "all":
        wire["body"].pop("usage")
        wire["body"].pop("id")
    elif missing == "usage_output":
        wire["body"]["usage"].pop("output_tokens")
    else:
        wire["body"].pop("id")
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        result = provider.generate(request())
    if missing in {"all", "id"}:
        assert result.response_id is None
    if missing == "all":
        assert result.input_tokens is None and result.output_tokens is None
    if missing == "usage_output":
        assert result.input_tokens == 41 and result.output_tokens is None


@pytest.mark.parametrize(
    "status,code,param,expected",
    [
        (401, None, None, "authentication"),
        (403, None, None, "authentication"),
        (402, None, None, "quota"),
        (429, "insufficient_quota", None, "quota"),
        (429, None, None, "rate_limit"),
        (404, "model_not_found", None, "model_unavailable"),
        (404, None, None, "invalid_request"),
        (404, "invalid_json_schema", None, "structured_output"),
        (500, None, None, "unavailable"),
        (502, None, None, "unavailable"),
        (503, "service_unavailable_error", None, "unavailable"),
        (504, None, None, "unavailable"),
        (599, "model_not_found", None, "unavailable"),
        (400, None, "text.format", "structured_output"),
        (400, "invalid_json_schema", None, "structured_output"),
        (400, None, None, "invalid_request"),
    ],
)
def test_safe_http_error_categories_and_zero_retry(wire, caplog, status, code, param, expected):
    caplog.set_level(logging.DEBUG)
    wire["status"] = status
    wire["body"] = {
        "error": {
            "message": "synthetic-secret PRIVATE_SOURCE_SENTINEL",
            "code": code,
            "param": param,
        }
    }
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        with pytest.raises(AnalysisProviderFailure) as error:
            provider.generate(request())
    assert error.value.code == "provider_" + expected
    assert "synthetic-secret" not in str(error.value) + caplog.text
    assert "PRIVATE_SOURCE_SENTINEL" not in str(error.value) + caplog.text
    assert error.value.__suppress_context__ and len(wire["calls"]) == 1


@pytest.mark.parametrize(
    "error_type,code",
    [
        (httpx.ReadTimeout, "provider_timeout"),
        (httpx.ConnectError, "provider_network"),
    ],
)
def test_network_failures_are_safe(wire, error_type, code):
    wire["error"] = error_type
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        with pytest.raises(AnalysisProviderFailure) as error:
            provider.generate(request())
    assert error.value.code == code and len(wire["calls"]) == 1


@pytest.mark.parametrize(
    "case,code",
    [
        ("incomplete", "provider_incomplete"),
        ("refusal", "provider_refusal"),
        ("failed", "provider_model_unavailable"),
        ("empty", "provider_empty_json"),
        ("bad_usage", "provider_sdk_incompatible"),
    ],
)
def test_non_success_responses_are_rejected(wire, case, code):
    if case == "incomplete":
        wire["body"]["status"] = "incomplete"
    elif case == "refusal":
        wire["body"]["output"][1]["content"] = [{"type": "refusal", "refusal": "private reason"}]
    elif case == "failed":
        wire["body"]["status"] = "failed"
        wire["body"]["error"] = {"code": "model_not_found", "message": "private provider message"}
    elif case == "empty":
        wire["body"]["output"] = []
    else:
        wire["body"]["usage"]["input_tokens"] = -1
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        with pytest.raises(AnalysisProviderFailure) as error:
            provider.generate(request())
    assert error.value.code == code


@pytest.mark.parametrize(
    "output,expected_error,expected_code",
    [
        ("invalid json", AnalysisProviderFailure, "provider_invalid_json"),
        (json.dumps(valid_output("S99")), AnalysisOutputValidationError, "invalid_output"),
        (json.dumps({"summary": "bad schema"}), AnalysisOutputValidationError, "invalid_output"),
    ],
)
def test_service_keeps_strict_validation_with_deepseek(
    wire, database, projects, questions, links, output, expected_error, expected_code
):
    question = questions.create(projects.create("Synthetic project").id, "Synthetic question")
    links.attach_reference(question.id, "FAKE0001")
    wire["body"]["output"][1]["content"][0]["text"] = output
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        service = AnalysisService(database, ResearchCorpusBuilder(FakeZoteroClient()), provider)
        with pytest.raises(expected_error):
            service.run_analysis(question.id)
    run = AnalysisRepository(database).list_by_question(question.id)[0]
    assert run.status == "failed" and run.error_code == expected_code


def test_provider_identity_and_categorized_failure_persist_without_service_branch(
    wire, database, projects, questions, links
):
    question = questions.create(projects.create("Synthetic project").id, "Synthetic question")
    link = links.attach_reference(question.id, "FAKE0001")
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        service = AnalysisService(database, ResearchCorpusBuilder(FakeZoteroClient()), provider)
        completed = service.run_analysis(question.id)
        assert completed.provider == "deepseek" and completed.model == ENV["DEEPSEEK_MODEL"]
        assert json.loads(completed.result_json)["summary"]["source_ids"] == [link.source_id]
        wire["status"] = 401
        wire["body"] = {"error": {"message": "synthetic-secret"}}
        with pytest.raises(AnalysisProviderFailure):
            service.run_analysis(question.id)
    runs = AnalysisRepository(database).list_by_question(question.id)
    assert runs[1].error_code == "provider_authentication" and runs[1].status == "failed"
    assert "synthetic-secret" not in runs[1].error_message
    assert links.list_for_question(question.id) == (link,)
    assert AnalysisRepository(database).get(completed.id) == completed


@pytest.mark.parametrize(
    "env", [{}, {"RUN_DEEPSEEK_INTEGRATION": "1"}, ENV, {**ENV, "RUN_DEEPSEEK_INTEGRATION": "0"}]
)
def test_real_test_guard_cannot_run_without_explicit_configuration(env):
    with pytest.raises(pytest.skip.Exception):
        require_deepseek_config(env)


@pytest.mark.parametrize(
    "env", [{}, {"ZOTERO_TEST_ITEM_KEY": "FAKE0001"}, {"RUN_DEEPSEEK_ZOTERO_E2E": "1"}]
)
def test_abstract_transmission_requires_both_specific_consent_and_explicit_item(env):
    with pytest.raises(pytest.skip.Exception):
        consented_zotero_item(env)


def test_real_test_gates_accept_explicit_consent_only():
    require_deepseek_config({**ENV, "RUN_DEEPSEEK_INTEGRATION": "1"})
    assert (
        consented_zotero_item({"ZOTERO_TEST_ITEM_KEY": "FAKE0001", "RUN_DEEPSEEK_ZOTERO_E2E": "1"})
        == "FAKE0001"
    )


@pytest.mark.parametrize(
    "text,code",
    [
        ("", "provider_empty_json"),
        (" \n\t", "provider_empty_json"),
        ("not JSON", "provider_invalid_json"),
        ("```json\n{}\n```", "provider_invalid_json"),
        ("prefix {}", "provider_invalid_json"),
        ("{} trailing", "provider_invalid_json"),
        ('{"x":NaN}', "provider_invalid_json"),
        ('{"x":Infinity}', "provider_invalid_json"),
        ('{"x":-Infinity}', "provider_invalid_json"),
        (" " * 200000 + "{}", "provider_invalid_json"),
        ("[" * 2000, "provider_invalid_json"),
    ],
)
def test_json_failures_are_safe_without_repair(wire, caplog, text, code):
    caplog.set_level(logging.DEBUG)
    wire["body"]["output"][1]["content"][0]["text"] = text
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        with pytest.raises(AnalysisProviderFailure) as error:
            provider.generate(request())
    assert error.value.code == code and error.value.__suppress_context__
    assert len(wire["calls"]) == 1
    assert "PRIVATE_SOURCE_SENTINEL" not in caplog.text
    assert "synthetic-secret" not in str(error.value) + caplog.text


@pytest.mark.parametrize(
    "case,code",
    [
        ("tool", "provider_sdk_incompatible"),
        ("unknown_output", "provider_sdk_incompatible"),
        ("unknown_part", "provider_sdk_incompatible"),
        ("user", "provider_sdk_incompatible"),
        ("message_running", "provider_sdk_incompatible"),
        ("message_incomplete", "provider_incomplete"),
        ("message_missing_status", "provider_sdk_incompatible"),
        ("message_missing_role", "provider_sdk_incompatible"),
        ("multiple_messages", "provider_sdk_incompatible"),
        ("multiple_parts", "provider_sdk_incompatible"),
        ("missing_content", "provider_sdk_incompatible"),
        ("bad_content", "provider_sdk_incompatible"),
        ("null_text", "provider_sdk_incompatible"),
        ("missing_output", "provider_sdk_incompatible"),
        ("bad_output", "provider_sdk_incompatible"),
        ("bad_object", "provider_sdk_incompatible"),
        ("completed_with_error", "provider_sdk_incompatible"),
        ("unknown_status", "provider_sdk_incompatible"),
        ("queued", "provider_sdk_incompatible"),
        ("empty_content", "provider_empty_json"),
        ("reasoning_only", "provider_empty_json"),
    ],
)
def test_unexpected_responses_output_is_rejected(wire, case, code):
    body = wire["body"]
    message = body["output"][1]
    if case == "tool":
        body["output"].append(
            {
                "type": "function_call",
                "id": "fake",
                "call_id": "fake",
                "name": "unexpected",
                "arguments": "{}",
            }
        )
    elif case == "unknown_output":
        body["output"].append({"type": "unknown_private_type"})
    elif case == "unknown_part":
        message["content"].append({"type": "unknown_private_type"})
    elif case == "user":
        message["role"] = "user"
    elif case.startswith("message_"):
        if case == "message_running":
            message["status"] = "in_progress"
        elif case == "message_incomplete":
            message["status"] = "incomplete"
        elif case == "message_missing_status":
            message.pop("status")
        else:
            message.pop("role")
    elif case == "multiple_messages":
        body["output"].append(copy.deepcopy(message))
    elif case == "multiple_parts":
        message["content"].append(copy.deepcopy(message["content"][0]))
    elif case == "missing_content":
        message.pop("content")
    elif case == "bad_content":
        message["content"] = None
    elif case == "null_text":
        message["content"][0]["text"] = None
    elif case == "missing_output":
        body.pop("output")
    elif case == "bad_output":
        body["output"] = "private-bad-output"
    elif case == "bad_object":
        body["object"] = "chat.completion"
    elif case == "completed_with_error":
        body["error"] = {"code": "server_error", "message": "private-error"}
    elif case == "unknown_status":
        body["status"] = "private-unknown-status"
    elif case == "queued":
        body["status"] = "queued"
    elif case == "empty_content":
        message["content"] = []
    else:
        body["output"] = body["output"][:1]
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        with pytest.raises(AnalysisProviderFailure) as error:
            provider.generate(request())
    assert error.value.code == code and len(wire["calls"]) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 42),
        ("id", "unsafe id"),
        ("id", ""),
        ("id", "x" * 257),
        ("input_tokens", -1),
        ("output_tokens", -1),
        ("input_tokens", True),
        ("output_tokens", "20"),
        ("output_tokens", 1.5),
        ("usage", "unexpected-usage"),
    ],
)
def test_invalid_metadata_is_not_normalized(wire, field, value):
    if field in {"input_tokens", "output_tokens"}:
        wire["body"]["usage"][field] = value
    else:
        wire["body"][field] = value
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        with pytest.raises(AnalysisProviderFailure) as error:
            provider.generate(request())
    assert error.value.code == "provider_sdk_incompatible"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("model_not_found", "provider_model_unavailable"),
        ("insufficient_quota", "provider_quota"),
        ("invalid_json_schema", "provider_structured_output"),
        ("server_error", "provider_unavailable"),
        ("service_unavailable_error", "provider_unavailable"),
        ("unknown_private_code", "provider_failed"),
    ],
)
def test_failed_response_error_still_persists_safely(
    wire, database, projects, questions, links, caplog, code, expected
):
    from research_workbench.analysis.errors import AnalysisProviderError

    caplog.set_level(logging.DEBUG)
    question = questions.create(projects.create("Synthetic project").id, "Synthetic question")
    links.attach_reference(question.id, "FAKE0001")
    wire["body"].update(
        status="failed", error={"code": code, "message": "synthetic-secret PRIVATE_SOURCE_SENTINEL"}
    )
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        with pytest.raises(AnalysisProviderError):
            AnalysisService(
                database, ResearchCorpusBuilder(FakeZoteroClient()), provider
            ).run_analysis(question.id)
    run = AnalysisRepository(database).list_by_question(question.id)[0]
    assert run.status == "failed" and run.error_code == expected and run.result_json is None
    assert len(wire["calls"]) == 1
    assert "synthetic-secret" not in run.error_message + caplog.text
    assert "PRIVATE_SOURCE_SENTINEL" not in run.error_message + caplog.text


@pytest.mark.parametrize(
    "case,code",
    [
        ("503", "provider_unavailable"),
        ("404", "provider_invalid_request"),
        ("blank", "provider_empty_json"),
        ("invalid_json", "provider_invalid_json"),
        ("duplicate_key", "invalid_output"),
        ("duplicate_label", "invalid_output"),
        ("S99", "invalid_output"),
        ("incomplete", "provider_incomplete"),
        ("refusal", "provider_refusal"),
        ("unexpected", "provider_sdk_incompatible"),
        ("bad_id", "provider_sdk_incompatible"),
    ],
)
def test_success_then_failed_history_keeps_manifest_and_old_run(
    wire, database, projects, questions, links, caplog, case, code
):
    from research_workbench.analysis.errors import AnalysisError

    caplog.set_level(logging.DEBUG)
    reader = FakeZoteroClient()
    question = questions.create(projects.create("Synthetic project").id, "Synthetic question")
    link = links.attach_reference(question.id, "FAKE0001")
    repo = AnalysisRepository(database)
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        service = AnalysisService(database, ResearchCorpusBuilder(reader), provider)
        completed = service.run_analysis(question.id, "abstracts")
        content = json.loads(wire["calls"][0]["input"])["sources"][0]["content"]
        manifest = repo.get_sources(completed.id)
        assert manifest[0].content_sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert manifest[0].input_chars == len(content) and manifest[0].source_id == link.source_id
        assert completed.prompt_version == "analysis-v1"
        assert completed.schema_version == "analysis-schema-v1"
        if case in {"503", "404"}:
            wire["status"] = int(case)
            wire["body"] = {"error": {"message": "synthetic-secret PRIVATE_SOURCE_SENTINEL"}}
        elif case == "incomplete":
            wire["body"]["status"] = "incomplete"
        elif case == "refusal":
            wire["body"]["output"][1]["content"] = [{"type": "refusal", "refusal": "private"}]
        elif case == "unexpected":
            wire["body"]["output"].append({"type": "unexpected"})
        elif case == "bad_id":
            wire["body"]["id"] = "private invalid id"
        else:
            text = json.dumps(valid_output("S99" if case == "S99" else "S1"))
            if case == "blank":
                text = "  "
            elif case == "invalid_json":
                text = "PRIVATE_SOURCE_SENTINEL invalid JSON"
            elif case == "duplicate_key":
                text = text.replace('"summary":', '"summary":{},"summary":', 1)
            elif case == "duplicate_label":
                value = valid_output()
                value["summary"]["source_labels"] = ["S1", "S1"]
                text = json.dumps(value)
            wire["body"]["output"][1]["content"][0]["text"] = text
        with pytest.raises(AnalysisError):
            service.run_analysis(question.id)
    runs = repo.list_by_question(question.id)
    assert len(runs) == 2 and len(wire["calls"]) == 2  # Two explicit calls, no retry.
    failed = runs[1]
    assert failed.status == "failed" and failed.error_code == code and failed.result_json is None
    assert repo.get(completed.id) == completed and repo.get_sources(completed.id) == manifest
    assert repo.get_sources(failed.id)[0].content_sha256 == manifest[0].content_sha256
    for terminal in runs:
        with pytest.raises(AnalysisStateError):
            repo.fail_run(terminal.id, "analysis_failed")
        with pytest.raises(AnalysisStateError):
            repo.complete_run(terminal.id, completed.result_json)
        assert repo.get(terminal.id) == terminal
    stored = database.path.read_bytes()
    for raw in (reader.item.abstract, content, wire["calls"][0]["input"], "synthetic-secret"):
        assert raw.encode("utf-8") not in stored
    assert "synthetic-secret" not in caplog.text and "PRIVATE_SOURCE_SENTINEL" not in caplog.text
    assert reader.fulltext_calls == 0
