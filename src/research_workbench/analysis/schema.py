"""Strict JSON schema plus independent application validation and attribution."""

import json

from .errors import AnalysisOutputValidationError

POINT_FIELDS = ("main_approaches", "key_findings", "agreements", "disagreements", "research_gaps")
POINT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 2000},
        "source_labels": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string"},
        },
    },
    "required": ["text", "source_labels"],
}
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": POINT_SCHEMA,
        **{
            name: {
                "type": "array",
                "maxItems": 12,
                "minItems": 1 if name == "key_findings" else 0,
                "items": POINT_SCHEMA,
            }
            for name in POINT_FIELDS
        },
        "next_questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
    },
    "required": ["summary", *POINT_FIELDS, "next_questions"],
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisOutputValidationError()
        result[key] = value
    return result


def validate_and_map(raw: str, labels: dict[str, int]) -> str:
    """Return normalized JSON with SourceRef ids, never model-selected database ids."""

    def require(condition):
        if not condition:
            raise AnalysisOutputValidationError()

    def text(value, limit):
        require(isinstance(value, str) and 1 <= len(value.strip()) <= limit)
        return value.strip()

    def point(value):
        require(isinstance(value, dict) and set(value) == {"text", "source_labels"})
        citations = value["source_labels"]
        require(isinstance(citations, list) and 1 <= len(citations) <= 5)
        require(all(isinstance(label, str) and label in labels for label in citations))
        require(len(set(citations)) == len(citations))
        return {
            "text": text(value["text"], 2000),
            "source_ids": [labels[label] for label in citations],
        }

    try:
        require(isinstance(raw, str) and len(raw) <= 200_000)
        data = json.loads(raw, object_pairs_hook=_unique_object)
        require(isinstance(data, dict) and set(data) == set(OUTPUT_SCHEMA["required"]))
        result = {"summary": point(data["summary"])}
        for name in POINT_FIELDS:
            values = data[name]
            require(
                isinstance(values, list)
                and (1 if name == "key_findings" else 0) <= len(values) <= 12
            )
            result[name] = [point(value) for value in values]
        values = data["next_questions"]
        require(isinstance(values, list) and 1 <= len(values) <= 10)
        result["next_questions"] = [text(value, 1000) for value in values]
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise AnalysisOutputValidationError() from None


def validate_mapped_result(raw: str, labels: dict[str, int]) -> str:
    """Persistence defense: normalized ids must belong to this run's immutable manifest."""
    try:
        if not isinstance(raw, str) or len(raw) > 200_000:
            raise AnalysisOutputValidationError()
        data = json.loads(raw, object_pairs_hook=_unique_object)
        reverse = {source_id: label for label, source_id in labels.items()}

        def convert(point):
            if not isinstance(point, dict) or set(point) != {"text", "source_ids"}:
                raise AnalysisOutputValidationError()
            ids = point["source_ids"]
            if not isinstance(ids, list) or any(
                type(i) is not int or i not in reverse for i in ids
            ):
                raise AnalysisOutputValidationError()
            return {"text": point["text"], "source_labels": [reverse[i] for i in ids]}

        data["summary"] = convert(data["summary"])
        for name in POINT_FIELDS:
            if not isinstance(data[name], list):
                raise AnalysisOutputValidationError()
            data[name] = [convert(point) for point in data[name]]
        return validate_and_map(json.dumps(data), labels)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise AnalysisOutputValidationError() from None
