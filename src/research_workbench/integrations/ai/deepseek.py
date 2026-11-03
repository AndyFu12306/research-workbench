"""DeepSeek identity and Responses differences stay inside this adapter."""

import logging
import math
import os

import openai

from research_workbench.analysis.contracts import INSTRUCTIONS, AnalysisRequest, ProviderResult
from research_workbench.analysis.errors import (
    AnalysisConfigurationError,
    AnalysisProviderError,
    AnalysisProviderFailure,
)
from research_workbench.analysis.schema import OUTPUT_SCHEMA


def _request_failure(status: int | None, code: object, param: object) -> AnalysisProviderError:
    # Use typed status and known machine codes, never provider prose or raw payloads.
    if status == 401 or status == 403:
        kind = "authentication"
    elif status == 402 or code in ("insufficient_quota", "insufficient_balance", "quota_exceeded"):
        kind = "quota"
    elif status == 429:
        kind = "rate_limit"
    elif code in ("model_not_found", "model_not_available", "invalid_model") or param == "model":
        kind = "model_unavailable"
    elif status == 404:
        kind = "responses_incompatible"
    elif code in (
        "unsupported_response_format",
        "invalid_json_schema",
        "unsupported_json_schema",
    ) or param in ("text.format", "text.format.schema", "response_format"):
        kind = "structured_output"
    elif status in (400, 422):
        kind = "invalid_request"
    else:
        return AnalysisProviderError()
    return AnalysisProviderFailure("provider_" + kind)


def _token_count(usage, name: str) -> int | None:
    value = getattr(usage, name, None)
    if value is not None and (type(value) is not int or value < 0):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    return value


def _normalize(response) -> ProviderResult:
    status = getattr(response, "status", None)
    if status == "incomplete":
        raise AnalysisProviderFailure("provider_incomplete")
    if status == "failed":
        error = getattr(response, "error", None)
        raise _request_failure(None, getattr(error, "code", None), None)
    if status != "completed":
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue  # Do not retain reasoning text or treat it as the final analysis.
        for part in getattr(item, "content", ()):
            if getattr(part, "type", None) == "refusal":
                raise AnalysisProviderFailure("provider_refusal")
    text = response.output_text
    if not isinstance(text, str) or not text.strip():
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    usage = getattr(response, "usage", None)
    return ProviderResult(
        text,
        getattr(response, "id", None),
        _token_count(usage, "input_tokens"),
        _token_count(usage, "output_tokens"),
    )


class DeepSeekAnalysisProvider:
    provider = "deepseek"

    def __init__(self, *, environ=None) -> None:
        env = os.environ if environ is None else environ
        key, model = env.get("DEEPSEEK_API_KEY"), env.get("DEEPSEEK_MODEL")
        try:
            timeout = float(env.get("DEEPSEEK_TIMEOUT_SECONDS") or "60")
            if (
                not key
                or not key.strip()
                or not model
                or not model.strip()
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise AnalysisConfigurationError() from None
        self.model = model.strip()
        logging.getLogger("openai").setLevel(logging.WARNING)
        logging.getLogger("openai._base_client").setLevel(logging.WARNING)
        self._client = openai.OpenAI(
            api_key=key,
            base_url="https://api.deepseek.com",
            timeout=timeout,
            max_retries=0,
            organization="",
            project="",  # Never inherit OpenAI account headers for DeepSeek.
        )

    def generate(self, request: AnalysisRequest) -> ProviderResult:
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=INSTRUCTIONS,
                input=request.render_input(),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "research_analysis",
                        "schema": OUTPUT_SCHEMA,
                        "strict": True,
                    }
                },
                max_output_tokens=4096,
                # DeepSeek docs: stateless, store/truncation parameters are unsupported.
                # Local budget enforcement is unchanged; never fall back to plain text.
            )
        except openai.APITimeoutError:
            raise AnalysisProviderFailure("provider_timeout") from None
        except openai.APIConnectionError:
            raise AnalysisProviderFailure("provider_network") from None
        except openai.APIStatusError as exc:
            raise _request_failure(exc.status_code, exc.code, exc.param) from None
        except Exception:
            raise AnalysisProviderFailure("provider_sdk_incompatible") from None
        try:
            return _normalize(response)
        except AnalysisProviderError:
            raise
        except Exception:
            raise AnalysisProviderFailure("provider_sdk_incompatible") from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
