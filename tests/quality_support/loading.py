"""Strict JSON and typed decoding; only registered synthetic fixture paths are read."""

import hashlib
import json
import re
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Literal, get_args, get_origin, get_type_hints

from .contracts import BENCHMARK, Assessment, Candidate, Case, Manifest, QualityError, require

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "quality" / "v2"


def canonical(value):
    if is_dataclass(value):
        value = {f.name: getattr(value, f.name) for f in fields(value)}
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    return value


def encode(value):
    return json.dumps(
        canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _constant(_):
    raise QualityError()


def decode(raw):
    try:
        result = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
        # Reject lone surrogates, including JSON-escaped ones.
        encode(result).encode("utf-8")
        return result
    except (ValueError, TypeError, UnicodeError):
        raise QualityError() from None


def typed(cls, value):
    """Only the closed DTO vocabulary is supported; no coercion/default dropping."""
    origin, args = get_origin(cls), get_args(cls)
    if origin is Literal:
        require(any(type(value) is type(v) and value == v for v in args))
        return value
    if origin is tuple:
        require(type(value) is list)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(typed(args[0], v) for v in value)
        require(len(value) == len(args))
        return tuple(typed(t, v) for t, v in zip(args, value, strict=True))
    if is_dataclass(cls):
        hints = get_type_hints(cls)
        require(type(value) is dict and set(value) == set(hints))
        return cls(**{k: typed(t, value[k]) for k, t in hints.items()})
    require(cls in (str, int, bool) and type(value) is cls)
    if cls is str:
        require(not any("\ud800" <= c <= "\udfff" for c in value))
    return value


def load_case(case_id):
    require(type(case_id) is str and re.fullmatch(r"Q(?:0[1-9]|1[0-8])", case_id))
    from .validation import validate_case

    case = typed(Case, decode((ROOT / "cases" / f"{case_id}.json").read_text("utf-8")))
    validate_case(case)
    return case


def load_manifest():
    manifest = typed(Manifest, decode((ROOT / "manifest.json").read_text("utf-8")))
    require(manifest.version == BENCHMARK)
    require(tuple(e.case_id for e in manifest.cases) == tuple(f"Q{i:02}" for i in range(1, 19)))
    for entry in manifest.cases:
        require(bool(entry.candidates) and len(set(entry.candidates)) == len(entry.candidates))
        require(all(re.fullmatch(r"[a-z_]+", name) for name in entry.candidates))
    return manifest


def load_candidate(case, name):
    require(type(name) is str and re.fullmatch(r"[a-z_]+", name))
    from .validation import validate_candidate

    require(name in next(e.candidates for e in load_manifest().cases if e.case_id == case.case_id))

    candidate = typed(
        Candidate, decode((ROOT / "candidates" / f"{case.case_id}_{name}.json").read_text("utf-8"))
    )
    validate_candidate(case, candidate)
    require(candidate.candidate_id == f"{case.case_id}_{name}")
    return candidate


def load_assessment(case, candidate):
    from .validation import validate_assessment, validate_candidate, validate_case

    validate_case(case)
    validate_candidate(case, candidate)

    assessment = typed(
        Assessment,
        decode((ROOT / "assessments" / f"{candidate.candidate_id}.json").read_text("utf-8")),
    )
    validate_assessment(case, candidate, assessment)
    return assessment
