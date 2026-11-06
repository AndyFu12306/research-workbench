"""Fixed safe messages: never interpolate provider errors or source text."""


class AnalysisError(Exception):
    code = "analysis_failed"
    message = "Analysis failed."

    def __init__(self, *, source_id: int | None = None) -> None:
        self.source_id = source_id
        super().__init__(self.message + (f" SourceRef id={source_id}." if source_id else ""))


class AnalysisNoSourcesError(AnalysisError):
    code = "no_sources"
    message = "The Question has no linked sources."


class AnalysisCorpusIncompleteError(AnalysisError):
    code = "corpus_incomplete"
    message = "A linked source is missing or has no usable content."


class AnalysisRetrievalUnavailableError(AnalysisError):
    code = "source_retrieval_unavailable"
    message = "Current source retrieval is unavailable."


class AnalysisInputTooLargeError(AnalysisError):
    code = "input_too_large"
    message = "Input exceeds the configured analysis budget; no provider call was made."


class AnalysisOutputValidationError(AnalysisError):
    code = "invalid_output"
    message = "Provider output failed schema or source-attribution validation."


class AnalysisProviderError(AnalysisError):
    code = "provider_failed"
    message = "The analysis provider request failed."


PROVIDER_FAILURE_MESSAGES = {
    "provider_unavailable": "The provider service is temporarily unavailable.",
    "provider_invalid_json": "The provider returned invalid JSON.",
    "provider_empty_json": "The provider returned empty JSON output.",
    "provider_authentication": "Provider authentication or access was rejected.",
    "provider_rate_limit": "Provider rate limit reached.",
    "provider_quota": "Provider billing or quota is unavailable.",
    "provider_timeout": "The provider request timed out.",
    "provider_network": "The provider network connection failed.",
    "provider_model_unavailable": "The configured provider model is unavailable.",
    "provider_invalid_request": "The provider rejected the request parameters.",
    "provider_context_length": "Model context exceeded. Use hierarchical Reduce; no retry.",
    "provider_structured_output": "The provider rejected structured output configuration.",
    "provider_responses_incompatible": "The provider Responses endpoint is unavailable.",
    "provider_sdk_incompatible": "The provider response could not be normalized safely.",
    "provider_incomplete": "The provider returned an incomplete response.",
    "provider_refusal": "The provider refused to produce the analysis.",
}


class AnalysisProviderFailure(AnalysisProviderError):
    """Only fixed, provider-neutral diagnostic codes may cross the adapter boundary."""

    def __init__(self, code: str) -> None:
        self.message = PROVIDER_FAILURE_MESSAGES[code]
        self.code = code
        super().__init__()


class AnalysisConfigurationError(AnalysisError):
    code = "invalid_configuration"
    message = "Configure a supported model, API key and positive finite timeout."


INCOMPLETE_REASONS = frozenset(("max_output_tokens", "content_filter"))


class AnalysisProviderIncomplete(AnalysisProviderFailure):
    """Only an allowlisted status reason; never retain the response or partial output."""

    def __init__(self, reason=None):
        self.reason = reason if type(reason) is str and reason in INCOMPLETE_REASONS else None
        super().__init__("provider_incomplete")


class AnalysisStateError(AnalysisError):
    code = "immutable_run"
    message = "Only a running analysis can be finalized; history is immutable."


class AnalysisPersistenceError(AnalysisError):
    code = "persistence_failed"
    message = "Analysis persistence failed; check the Workbench database."


VALIDATION_FAILURE_MESSAGES = {
    "invalid_output_json": "Output JSON is invalid, duplicated or non-standard.",
    "invalid_output_structure": "Output top-level fields or status are invalid.",
    "invalid_output_finding_count": "Finding count or empty-result status is invalid.",
    "invalid_output_finding_structure": "Finding fields or field types are invalid.",
    "invalid_output_finding_field": "Finding text, kind or category violates the contract.",
    "invalid_output_citation_format": "Citation list, count, type or uniqueness is invalid.",
    "invalid_output_citation_unknown": "Citation does not name a permitted local input.",
    "invalid_output_source_binding": "Stage input or source fingerprint binding is invalid.",
    "invalid_output_result_integrity": "A validated child result failed replay integrity checks.",
    "invalid_output_persistence_binding": "Stage result does not match its persisted binding.",
}


FAILURE_MESSAGES = {
    **PROVIDER_FAILURE_MESSAGES,
    **VALIDATION_FAILURE_MESSAGES,
    "provider_failed": AnalysisProviderError.message,
    "invalid_output": AnalysisOutputValidationError.message,
    "analysis_failed": AnalysisError.message,
    "persistence_failed": AnalysisPersistenceError.message,
}
