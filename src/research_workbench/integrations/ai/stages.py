"""Opt-in single stage calls; existing v1 adapters/methods remain unchanged."""

import json
from dataclasses import dataclass

import openai

from research_workbench.analysis.contracts import ProviderResult
from research_workbench.analysis.errors import AnalysisProviderError, AnalysisProviderFailure
from research_workbench.analysis.stage_contracts import (
    CONTRACT_VERSIONS,
    StageBudgetError,
    StageCapabilityError,
    StageError,
    StageRequest,
    stage_instructions,
    stage_schema,
)
from research_workbench.analysis.stage_validation import render_stage_input

from .deepseek import DeepSeekAnalysisProvider
from .deepseek import _normalize as normalize_response
from .deepseek import _request_failure as request_failure
from .openai import OpenAIAnalysisProvider


@dataclass(frozen=True, slots=True)
class StageEstimate:
    """UTF-8 byte estimate, not tokenizer output, actual usage or a context guarantee."""

    input_tokens: int
    context_tokens: int
    estimator_version: str = "stage-request-utf8-bytes-v1"


def prepare_stage_call(
    request: StageRequest, *, model: str, provider: str
) -> tuple[dict, StageEstimate]:
    """Build and budget the very payload passed to SDK responses.create; no I/O."""
    if (
        type(request) is not StageRequest
        or type(request.stage) is not str
        or type(request.contract_version) is not str
        or (request.stage, request.contract_version) not in _StageCalls.capabilities
        or provider not in ("openai", "deepseek")
    ):
        raise StageCapabilityError()
    if (
        type(model) is not str
        or not 1 <= len(model) <= 256
        or not model.strip()
        or any("\ud800" <= char <= "\udfff" for char in model)
    ):
        raise StageError() from None
    rendered = render_stage_input(request)
    payload = {
        "model": model,
        "instructions": stage_instructions(request.stage),
        "input": rendered,
        "text": {
            "format": {
                "type": "json_schema",
                "name": request.stage,
                "schema": stage_schema(request.stage, request.contract_version),
                "strict": True,
            }
        },
        "max_output_tokens": request.budget.output_tokens,
    }
    if provider == "openai":
        payload.update(store=False, truncation="disabled")
    # All final SDK arguments, including JSON escaping/schema/model/protocol wrappers.
    # HTTP headers/framing are represented by the explicit protocol reserve.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    estimated_input = len(encoded.encode("utf-8")) + request.budget.protocol_overhead_tokens
    estimated_context = (
        estimated_input + request.budget.output_tokens + request.budget.safety_margin_tokens
    )
    if (
        estimated_input > request.budget.max_input_tokens
        or estimated_context > request.budget.context_tokens
    ):
        raise StageBudgetError()
    return payload, StageEstimate(estimated_input, estimated_context)


class _StageCalls:
    capabilities = frozenset(CONTRACT_VERSIONS.items())

    def generate_stage(self, request: StageRequest) -> ProviderResult:
        if (
            type(request) is not StageRequest
            or type(request.stage) is not str
            or type(request.contract_version) is not str
            or (request.stage, request.contract_version) not in self.capabilities
        ):
            raise StageCapabilityError()
        payload, _ = prepare_stage_call(request, model=self.model, provider=self.provider)
        try:
            response = self._client.responses.create(**payload)
        except openai.APITimeoutError:
            raise AnalysisProviderFailure("provider_timeout") from None
        except openai.APIConnectionError:
            raise AnalysisProviderFailure("provider_network") from None
        except openai.APIStatusError as exc:
            raise request_failure(exc.status_code, exc.code, exc.param) from None
        except Exception:
            raise AnalysisProviderFailure("provider_sdk_incompatible") from None
        # Reuse the existing strict Responses shape/metadata checker for both new adapters.
        # It is provider neutral; v1 OpenAI normalization is intentionally left unchanged.
        try:
            return normalize_response(response)
        except AnalysisProviderError as exc:
            raise exc from None
        except Exception:
            raise AnalysisProviderFailure("provider_sdk_incompatible") from None


class OpenAIStageProvider(_StageCalls, OpenAIAnalysisProvider):
    """Separate opt-in stage capability using the existing OpenAI client configuration."""


class DeepSeekStageProvider(_StageCalls, DeepSeekAnalysisProvider):
    """Separate opt-in stage capability using the existing DeepSeek client configuration."""
