import json

import httpx
import pytest
from analysis_fakes import FakeZoteroClient, valid_output

from research_workbench.analysis.contracts import AnalysisRequest, CorpusSource
from research_workbench.analysis.errors import AnalysisConfigurationError, AnalysisProviderError
from research_workbench.integrations.ai import openai as adapter


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"OPENAI_API_KEY": "fake"},
        {"OPENAI_API_KEY": "fake", "OPENAI_MODEL": "test", "OPENAI_TIMEOUT_SECONDS": "nan"},
        {"OPENAI_API_KEY": "fake", "OPENAI_MODEL": "test", "OPENAI_TIMEOUT_SECONDS": "0"},
    ],
)
def test_configuration_is_required_without_env_loading(env):
    with pytest.raises(AnalysisConfigurationError):
        adapter.OpenAIAnalysisProvider(environ=env)


@pytest.mark.parametrize("scenario", ["success", "http_error", "incomplete", "refusal"])
def test_official_sdk_transport_contract_and_zero_retry(monkeypatch, scenario, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        if scenario == "http_error":
            return httpx.Response(429, json={"error": {"message": "PRIVATE_KEY_PROMPT_PAYLOAD"}})
        response = {
            "id": "resp_synthetic",
            "object": "response",
            "created_at": 0,
            "status": "incomplete" if scenario == "incomplete" else "completed",
            "model": "synthetic-model",
            "output": [
                {
                    "type": "message",
                    "id": "msg_fake",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "refusal", "refusal": "declined"}
                        if scenario == "refusal"
                        else {
                            "type": "output_text",
                            "text": json.dumps(valid_output()),
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19},
        }
        return httpx.Response(200, json=response)

    real_factory = adapter.openai.OpenAI

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 17
        return real_factory(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport))
        )

    monkeypatch.setattr(adapter.openai, "OpenAI", factory)
    with adapter.OpenAIAnalysisProvider(
        environ={
            "OPENAI_API_KEY": "fake-unit-test",
            "OPENAI_MODEL": "synthetic-model",
            "OPENAI_TIMEOUT_SECONDS": "17",
        }
    ) as provider:
        request = AnalysisRequest(
            "synthetic question",
            (CorpusSource(1, "S1", "abstract", FakeZoteroClient().item.abstract),),
        )
        if scenario == "success":
            result = provider.generate(request)
            assert result.input_tokens == 12 and result.output_tokens == 7
            assert result.response_id == "resp_synthetic"
        else:
            with pytest.raises(AnalysisProviderError) as error:
                provider.generate(request)
            assert "PRIVATE_KEY" not in str(error.value) and error.value.__suppress_context__
        assert len(calls) == 1
        sent = calls[0]
        assert sent["model"] == "synthetic-model" and sent["store"] is False
        assert sent["truncation"] == "disabled" and sent["max_output_tokens"] == 4096
        assert sent["text"]["format"]["strict"] is True
        assert sent["text"]["format"]["type"] == "json_schema"
        assert "source_id" not in sent["input"] and "ONLY" in sent["instructions"]
        assert "PRIVATE_SOURCE_SENTINEL" not in caplog.text
        assert "PRIVATE_KEY_PROMPT_PAYLOAD" not in caplog.text
