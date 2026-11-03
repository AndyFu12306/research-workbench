"""Official Responses API; env configuration, strict output, no automatic retries."""

import logging
import math
import os

import openai

from research_workbench.analysis.contracts import INSTRUCTIONS, AnalysisRequest, ProviderResult
from research_workbench.analysis.errors import AnalysisConfigurationError, AnalysisProviderError
from research_workbench.analysis.schema import OUTPUT_SCHEMA


class OpenAIAnalysisProvider:
    provider = "openai"

    def __init__(self, *, environ=None) -> None:
        env = os.environ if environ is None else environ
        key, model = env.get("OPENAI_API_KEY"), env.get("OPENAI_MODEL")
        try:
            timeout = float(env.get("OPENAI_TIMEOUT_SECONDS", "60"))
            if (
                not key
                or not model
                or not model.strip()
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise AnalysisConfigurationError() from None
        self.model = model.strip()
        # SDK debug logs include full request bodies. Keep them out even with OPENAI_LOG=debug.
        logging.getLogger("openai").setLevel(logging.WARNING)
        logging.getLogger("openai._base_client").setLevel(logging.WARNING)
        # SDK uses its official default endpoint; no hardcoded personal configuration.
        self._client = openai.OpenAI(
            api_key=key,
            organization=None,
            project=None,
            timeout=timeout,
            max_retries=0,
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
                store=False,
                truncation="disabled",
            )
            if response.status != "completed" or not response.output_text:
                raise AnalysisProviderError()
            return ProviderResult(
                response.output_text,
                response.id,
                response.usage.input_tokens if response.usage else None,
                response.usage.output_tokens if response.usage else None,
            )
        except Exception:
            raise AnalysisProviderError() from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
