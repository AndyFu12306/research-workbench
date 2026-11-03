"""DeepSeek Chat JSON mode; business schema/citation validation stays in the application."""

import json
import logging
import math
import os

import openai

from research_workbench.analysis.contracts import (
    INSTRUCTIONS,
    PROMPT_VERSION,
    AnalysisRequest,
    ProviderResult,
)
from research_workbench.analysis.errors import (
    AnalysisConfigurationError,
    AnalysisProviderError,
    AnalysisProviderFailure,
)
from research_workbench.analysis.schema import OUTPUT_SCHEMA

DEEPSEEK_PROMPT_VERSION = PROMPT_VERSION + ".deepseek-json-v1"
JSON_SERIALIZATION_INSTRUCTION = "Return one JSON object matching this schema. No Markdown:"


def _system_message() -> str:
    # Reuse the source-of-truth schema instead of copying the business prompt or examples.
    schema = json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    return INSTRUCTIONS + "\n" + JSON_SERIALIZATION_INSTRUCTION + "\n" + schema


def _request_failure(status: int | None, code: object, param: object) -> AnalysisProviderError:
    # Use typed status and known machine codes, never provider prose or raw payloads.
    if status is not None and 500 <= status <= 599:
        kind = "unavailable"
    elif status == 401 or status == 403:
        kind = "authentication"
    elif status == 402 or code in ("insufficient_quota", "insufficient_balance", "quota_exceeded"):
        kind = "quota"
    elif status == 429:
        kind = "rate_limit"
    elif code in ("model_not_found", "model_not_available", "invalid_model") or param == "model":
        kind = "model_unavailable"
    elif status == 404:
        kind = "invalid_request"
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


def _invalid_constant(_value):
    raise ValueError("Non-standard JSON constant")


def _normalize(response) -> ProviderResult:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    choice = choices[0]
    reason = getattr(choice, "finish_reason", None)
    message = getattr(choice, "message", None)
    if reason == "insufficient_system_resource":
        raise AnalysisProviderFailure("provider_unavailable")
    if reason in {"length", "aborted"}:
        raise AnalysisProviderFailure("provider_incomplete")
    if reason == "content_filter" or getattr(message, "refusal", None):
        raise AnalysisProviderFailure("provider_refusal")
    if (
        reason != "stop"
        or getattr(message, "role", None) != "assistant"
        or getattr(message, "tool_calls", None)
    ):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    text = getattr(message, "content", None)
    if text is None or (isinstance(text, str) and not text.strip()):
        raise AnalysisProviderFailure("provider_empty_json")
    if not isinstance(text, str):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    if len(text) > 200_000:
        raise AnalysisProviderFailure("provider_invalid_json")
    try:
        json.loads(text, parse_constant=_invalid_constant)
    except (ValueError, TypeError, RecursionError):
        raise AnalysisProviderFailure("provider_invalid_json") from None
    # Keep the original JSON: the application independently rejects bad schema,
    # duplicate keys and unknown/duplicate source labels. No extraction/repair here.
    usage = getattr(response, "usage", None)
    return ProviderResult(
        text,
        getattr(response, "id", None),
        _token_count(usage, "prompt_tokens"),
        _token_count(usage, "completion_tokens"),
    )


class DeepSeekAnalysisProvider:
    provider = "deepseek"
    prompt_version = DEEPSEEK_PROMPT_VERSION

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
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _system_message()},
                    {"role": "user", "content": request.render_input()},
                ],
                response_format={"type": "json_object"},
                max_tokens=4096,
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
