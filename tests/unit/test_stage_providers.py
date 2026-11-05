import copy
import json
import logging
from dataclasses import replace

import httpx
import openai
import pytest
from stage_fakes import map_request, mapped, output, reduce_request, reduced

from research_workbench.analysis.errors import AnalysisProviderFailure
from research_workbench.analysis.stage_contracts import (
    CONTRACT_VERSIONS,
    StageBudgetError,
    StageCapabilityError,
    StageError,
)
from research_workbench.analysis.stage_validation import render_stage_input, validate_stage_output
from research_workbench.integrations.ai import stages


@pytest.fixture(params=["openai", "deepseek"])
def wire(request, monkeypatch):
    provider = request.param
    state = {"calls": [], "status": 200, "error": None, "provider": provider}
    state["body"] = {
        "id": "synthetic-stage-response",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "configured-stage-model",
        "output": [
            {
                "type": "message",
                "id": "synthetic-message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "annotations": [],
                        "text": output((map_request().chunks[0].chunk_id,)),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 31, "output_tokens": 17, "total_tokens": 48},
    }

    def transport(req):
        state["calls"].append(json.loads(req.content))
        assert (
            str(req.url)
            == {
                "openai": "https://api.openai.com/v1/responses",
                "deepseek": "https://api.deepseek.com/responses",
            }[provider]
        )
        assert req.headers["Authorization"] == "Bearer synthetic-stage-key"
        if provider == "deepseek":
            assert not req.headers.get("OpenAI-Organization")
            assert not req.headers.get("OpenAI-Project")
        if state["error"]:
            raise state["error"]("PRIVATE_STAGE_ERROR synthetic-stage-key", request=req)
        return httpx.Response(state["status"], json=state["body"])

    original = openai.OpenAI

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return original(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport))
        )

    monkeypatch.setattr(openai, "OpenAI", factory)
    monkeypatch.setenv("OPENAI_ORG_ID", "private-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "private-project")
    prefix = provider.upper()
    env = {f"{prefix}_API_KEY": "synthetic-stage-key", f"{prefix}_MODEL": "configured-stage-model"}
    cls = stages.OpenAIStageProvider if provider == "openai" else stages.DeepSeekStageProvider
    with cls(environ=env) as instance:
        state["instance"] = instance
        yield state


@pytest.mark.parametrize("stage", list(CONTRACT_VERSIONS))
def test_actual_sdk_request_for_each_stage(wire, stage, caplog):
    caplog.set_level(logging.DEBUG)
    if stage == "map":
        request = map_request()
        citations = (request.chunks[0].chunk_id,)
    elif stage == "document_reduce":
        request = reduce_request((mapped(),))
        citations = (request.children[0].findings[0].finding_id,)
    else:
        request = reduce_request(
            (reduced(), reduced((mapped(map_request("FAKE0002")),))), cross=True
        )
        citations = tuple(child.findings[0].finding_id for child in request.children)
    raw = output(citations)
    wire["body"]["output"][0]["content"][0]["text"] = raw
    expected, estimate = stages.prepare_stage_call(
        request,
        model="configured-stage-model",
        provider=wire["provider"],
    )
    result = wire["instance"].generate_stage(request)
    assert wire["calls"] == [expected]
    assert expected["text"]["format"]["strict"] is True
    assert expected["text"]["format"]["name"] == stage
    assert expected["max_output_tokens"] == request.budget.output_tokens
    assert "tools" not in expected and "map-plan-envelope-v1" not in expected["input"]
    if wire["provider"] == "openai":
        assert expected["store"] is False and expected["truncation"] == "disabled"
    else:
        assert "store" not in expected and "truncation" not in expected
    assert result.output_json == raw and result.input_tokens == 31 and result.output_tokens == 17
    assert validate_stage_output(request, result.output_json).findings
    encoded = json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert estimate.input_tokens == len(encoded) + request.budget.protocol_overhead_tokens
    for private in ("PRIVATE_STAGE", "synthetic-stage-key"):
        assert private not in caplog.text and private not in repr(result)
    if stage != "map":
        assert "PRIVATE_STAGE_SOURCE" not in expected["input"]
        assert "PRIVATE_STAGE_FINDING" in expected["input"]


@pytest.mark.parametrize("change", ["stage", "version", "capabilities", "invalid_input"])
def test_preflight_failure_makes_zero_requests(wire, change):
    request = map_request()
    expected = StageCapabilityError
    if change == "stage":
        request = replace(request, stage="unsupported")
    elif change == "version":
        request = replace(request, contract_version="map-schema-future")
    elif change == "capabilities":
        wire["instance"].capabilities = frozenset()
    else:
        request = replace(request, question="\ud800PRIVATE_STAGE")
        expected = StageError
    with pytest.raises(expected):
        wire["instance"].generate_stage(request)
    assert not wire["calls"]


def test_final_payload_budget_boundary_and_output_reservation(wire):
    request = map_request()
    _, estimate = stages.prepare_stage_call(
        request, model=wire["instance"].model, provider=wire["provider"]
    )
    budget = replace(
        request.budget,
        max_input_tokens=estimate.input_tokens,
        context_tokens=estimate.context_tokens,
    )
    exact = replace(request, budget=budget)
    wire["instance"].generate_stage(exact)
    assert len(wire["calls"]) == 1
    for changed in (
        replace(budget, max_input_tokens=budget.max_input_tokens - 1),
        replace(budget, context_tokens=budget.context_tokens - 1),
    ):
        with pytest.raises(StageBudgetError):
            wire["instance"].generate_stage(replace(request, budget=changed))
    assert len(wire["calls"]) == 1
    # Counting only input/planning material would incorrectly accept this request.
    input_only = len(render_stage_input(request).encode("utf-8"))
    with pytest.raises(StageBudgetError):
        wire["instance"].generate_stage(
            replace(
                request,
                budget=replace(
                    budget, max_input_tokens=input_only + budget.protocol_overhead_tokens
                ),
            )
        )
    assert len(wire["calls"]) == 1


def test_reduce_budget_uses_actual_validated_text(wire):
    original = mapped()
    request = reduce_request((original,))
    raw = json.loads(original.output_json)
    raw["findings"][0]["text"] = "中文😀" * 450
    large = validate_stage_output(original.request, json.dumps(raw))
    larger = reduce_request((large,))
    _, small_estimate = stages.prepare_stage_call(
        request, model=wire["instance"].model, provider=wire["provider"]
    )
    _, large_estimate = stages.prepare_stage_call(
        larger, model=wire["instance"].model, provider=wire["provider"]
    )
    assert large_estimate.input_tokens > small_estimate.input_tokens
    budget = replace(larger.budget, max_input_tokens=small_estimate.input_tokens)
    with pytest.raises(StageBudgetError):
        wire["instance"].generate_stage(replace(larger, budget=budget))
    assert not wire["calls"]


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (401, None, "authentication"),
        (403, None, "authentication"),
        (402, None, "quota"),
        (429, None, "rate_limit"),
        (404, None, "invalid_request"),
        (400, "invalid_json_schema", "structured_output"),
        (404, "model_not_found", "model_unavailable"),
        (503, None, "unavailable"),
        (500, None, "unavailable"),
    ],
)
def test_safe_http_failures_zero_retry_never_empty_results(wire, status, code, expected, caplog):
    caplog.set_level(logging.DEBUG)
    wire.update(status=status, body={"error": {"message": "PRIVATE_STAGE_ERROR", "code": code}})
    with pytest.raises(AnalysisProviderFailure) as caught:
        wire["instance"].generate_stage(map_request())
    assert caught.value.code == "provider_" + expected
    assert len(wire["calls"]) == 1
    assert "PRIVATE_STAGE" not in caplog.text + str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ReadTimeout, "provider_timeout"),
        (httpx.ConnectError, "provider_network"),
    ],
)
def test_safe_network_failures_zero_retry(wire, error, code):
    wire["error"] = error
    with pytest.raises(AnalysisProviderFailure) as caught:
        wire["instance"].generate_stage(map_request())
    assert caught.value.code == code and len(wire["calls"]) == 1


@pytest.mark.parametrize(
    "change,code",
    [
        ("incomplete", "provider_incomplete"),
        ("refusal", "provider_refusal"),
        ("empty", "provider_empty_json"),
        ("fenced", "provider_invalid_json"),
        ("multiple", "provider_sdk_incompatible"),
        ("tool", "provider_sdk_incompatible"),
        ("role", "provider_sdk_incompatible"),
        ("usage", "provider_sdk_incompatible"),
        ("failed", "provider_unavailable"),
        ("nan", "provider_invalid_json"),
    ],
)
def test_strict_responses_shape_and_json(wire, change, code):
    body = wire["body"]
    message = body["output"][0]
    if change == "incomplete":
        body["status"] = "incomplete"
    elif change == "refusal":
        message["content"] = [{"type": "refusal", "refusal": "PRIVATE_STAGE_REFUSAL"}]
    elif change in ("empty", "fenced", "nan"):
        message["content"][0]["text"] = {
            "empty": " ",
            "fenced": "```json\n{}\n```",
            "nan": '{"x":NaN}',
        }[change]
    elif change == "multiple":
        body["output"].append(copy.deepcopy(message))
    elif change == "tool":
        body["output"] = [
            {"type": "function_call", "name": "unsafe", "call_id": "x", "arguments": "{}"}
        ]
    elif change == "role":
        message["role"] = "user"
    elif change == "usage":
        body["usage"]["output_tokens"] = -1
    else:
        body["status"] = "failed"
        body["error"] = {"code": "server_error", "message": "PRIVATE_STAGE_ERROR"}
    with pytest.raises(AnalysisProviderFailure) as caught:
        wire["instance"].generate_stage(map_request())
    assert caught.value.code == code and len(wire["calls"]) == 1


def test_candidate_remains_raw_for_duplicate_key_validation(wire):
    raw = '{"status":"no_relevant_findings","status":"no_relevant_findings","findings":[]}'
    wire["body"]["output"][0]["content"][0]["text"] = raw
    result = wire["instance"].generate_stage(map_request())
    assert result.output_json == raw
    with pytest.raises(StageError):
        validate_stage_output(map_request(), result.output_json)


def test_optional_metadata_and_ignored_reasoning(wire):
    wire["body"].pop("id")
    wire["body"].pop("usage")
    wire["body"]["output"].insert(0, {"type": "reasoning", "id": "synthetic", "summary": []})
    result = wire["instance"].generate_stage(map_request())
    assert (
        result.response_id is None and result.input_tokens is None and result.output_tokens is None
    )
