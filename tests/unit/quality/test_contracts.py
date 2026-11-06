"""Strict test-only benchmark contracts; never a production importer."""

from dataclasses import replace

import pytest
from quality_support.contracts import Case, QualityError
from quality_support.loading import (
    ROOT,
    canonical,
    decode,
    load_assessment,
    load_candidate,
    load_case,
    typed,
)
from quality_support.validation import validate_case


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_ambiguous_json_is_rejected(raw):
    with pytest.raises(ValueError, match="Invalid quality data"):
        decode(raw)


def test_registered_case_has_exact_evidence():
    case = load_case("Q13")
    assert case.case_id == "Q13"
    assert len(case.sources[0].text[:64]) == 64
    assert case.sources[0].text[64:].startswith("试验T")


MANIFEST = decode((ROOT / "manifest.json").read_text("utf-8"))


@pytest.mark.parametrize("entry", MANIFEST["cases"], ids=lambda x: x["case_id"])
def test_all_registered_cases_candidates_and_drafts(entry):
    case = load_case(entry["case_id"])
    assert case.language == ("zh" if int(case.case_id[1:]) % 2 else "en") or case.case_id in (
        "Q17",
        "Q18",
    )
    for name in entry["candidates"]:
        candidate = load_candidate(case, name)
        assessment = load_assessment(case, candidate)
        assert assessment.reviews == ()


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "missing",
        "bool_integer",
        "enum",
        "source_hash",
        "span",
        "empty_span",
        "dangling",
        "duplicate",
    ],
)
def test_invalid_data_fails_closed(change):
    data = canonical(load_case("Q01"))
    if change == "extra":
        data["payload"] = "private"
    if change == "missing":
        del data["revision"]
    if change == "bool_integer":
        data["revision"] = True
    if change == "enum":
        data["language"] = "other"
    if change == "source_hash":
        data["sources"][0]["sha256"] = "0" * 64
    if change == "span":
        data["facts"][0]["evidence"][0]["end"] = 9999
    if change == "empty_span":
        data["facts"][0]["evidence"][0]["end"] = 0
    if change == "dangling":
        data["relations"][0]["facts"][0] = "missing"
    if change == "duplicate":
        data["facts"].append(data["facts"][0])
    with pytest.raises(QualityError):
        validate_case(typed(Case, data))


@pytest.mark.parametrize("name", ["../Q01", "Q19", "", "C:/private"])
def test_no_arbitrary_case_paths(name):
    with pytest.raises(QualityError):
        load_case(name)


def test_evidence_indices_preserve_crlf_and_emoji():
    from quality_support.loading import text_hash

    case = load_case("Q01")
    text = "头\r\n😀证据\r\n尾"
    source = replace(case.sources[0], text=text, sha256=text_hash(text))
    span = replace(case.facts[0].evidence[0], start=3, end=6, sha256=text_hash("😀证据"))
    fact = replace(case.facts[0], evidence=(span,))
    validate_case(replace(case, sources=(source, case.sources[1]), facts=(fact, case.facts[1])))
