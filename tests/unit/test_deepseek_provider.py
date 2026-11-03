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
        (404, None, None, "responses_incompatible"),
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
        ("empty", "provider_sdk_incompatible"),
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
    "output",
    ["invalid json", json.dumps(valid_output("S99")), json.dumps({"summary": "bad schema"})],
)
def test_service_keeps_strict_validation_with_deepseek(
    wire, database, projects, questions, links, output
):
    question = questions.create(projects.create("Synthetic project").id, "Synthetic question")
    links.attach_reference(question.id, "FAKE0001")
    wire["body"]["output"][1]["content"][0]["text"] = output
    with adapter.DeepSeekAnalysisProvider(environ=ENV) as provider:
        service = AnalysisService(database, ResearchCorpusBuilder(FakeZoteroClient()), provider)
        with pytest.raises(AnalysisOutputValidationError):
            service.run_analysis(question.id)
    run = AnalysisRepository(database).list_by_question(question.id)[0]
    assert run.status == "failed" and run.error_code == "invalid_output"


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
