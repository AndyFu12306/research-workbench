"""DeepSeek identity and Responses differences stay inside this adapter."""

import json
import logging
import math
import os
import re

import openai

from research_workbench.analysis.contracts import INSTRUCTIONS, AnalysisRequest, ProviderResult
from research_workbench.analysis.errors import (
    AnalysisConfigurationError,
    AnalysisProviderError,
    AnalysisProviderFailure,
    AnalysisProviderIncomplete,
)
from research_workbench.analysis.schema import OUTPUT_SCHEMA


def _request_failure(status: int | None, code: object, param: object) -> AnalysisProviderError:
    # Use typed status and known machine codes, never provider prose or raw payloads.
    if (status is not None and 500 <= status <= 599) or (
        status is None
        and code
        in (
            "server_error",
            "service_unavailable",
            "service_unavailable_error",
            "insufficient_system_resource",
        )
    ):
        kind = "unavailable"
    elif status == 401 or status == 403:
        kind = "authentication"
    elif status == 402 or code in ("insufficient_quota", "insufficient_balance", "quota_exceeded"):
        kind = "quota"
    elif status == 429:
        kind = "rate_limit"
    elif code in (
        "context_length_exceeded",
        "context_window_exceeded",
        "max_context_length_exceeded",
    ):
        kind = "context_length"
    elif code in ("model_not_found", "model_not_available", "invalid_model") or param == "model":
        kind = "model_unavailable"
    elif code in (
        "unsupported_response_format",
        "invalid_json_schema",
        "unsupported_json_schema",
    ) or param in ("text.format", "text.format.schema", "response_format"):
        kind = "structured_output"
    elif status in (400, 404, 422):
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
    status = getattr(response, "status", None)
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        raise AnalysisProviderIncomplete(getattr(details, "reason", None))
    if status == "failed":
        error = getattr(response, "error", None)
        raise _request_failure(None, getattr(error, "code", None), None)
    if (
        status != "completed"
        or getattr(response, "object", None) != "response"
        or getattr(response, "error", None) is not None
    ):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    messages = []
    for item in output:
        kind = getattr(item, "type", None)
        if kind == "reasoning":
            continue  # Ignore reasoning metadata, never use it as final analysis.
        if kind != "message" or getattr(item, "role", None) != "assistant":
            raise AnalysisProviderFailure("provider_sdk_incompatible")
        if getattr(item, "status", None) == "incomplete":
            # A message-level status alone does not establish the response-level reason.
            raise AnalysisProviderIncomplete()
        if getattr(item, "status", None) != "completed":
            raise AnalysisProviderFailure("provider_sdk_incompatible")
        messages.append(item)
    if not messages:
        raise AnalysisProviderFailure("provider_empty_json")
    if len(messages) != 1:
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    content = getattr(messages[0], "content", None)
    if not isinstance(content, list):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    if any(getattr(part, "type", None) == "refusal" for part in content):
        raise AnalysisProviderFailure("provider_refusal")
    if not content:
        raise AnalysisProviderFailure("provider_empty_json")
    if len(content) != 1 or getattr(content[0], "type", None) != "output_text":
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    if not text.strip():
        raise AnalysisProviderFailure("provider_empty_json")
    if len(text) > 200_000:
        raise AnalysisProviderFailure("provider_invalid_json")
    try:
        json.loads(text, parse_constant=_invalid_constant)
    except (ValueError, TypeError, RecursionError):
        raise AnalysisProviderFailure("provider_invalid_json") from None
    # Do not reserialize: service must see the original duplicate keys and citation labels.
    response_id = getattr(response, "id", None)
    if response_id is not None and (
        not isinstance(response_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", response_id)
    ):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    usage = getattr(response, "usage", None)
    if usage is not None and not isinstance(usage, openai.types.responses.ResponseUsage):
        raise AnalysisProviderFailure("provider_sdk_incompatible")
    return ProviderResult(
        text,
        response_id,
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
        except AnalysisProviderError as exc:
            raise exc from None
        except Exception:
            raise AnalysisProviderFailure("provider_sdk_incompatible") from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
