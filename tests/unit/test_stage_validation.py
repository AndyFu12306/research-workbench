import json
from dataclasses import FrozenInstanceError, replace

import pytest
from stage_fakes import map_request, mapped, output, reduce_request, reduced

from research_workbench.analysis.chunking import SourceDocument, SourceIdentity, chunk_document
from research_workbench.analysis.stage_contracts import (
    CONTRACT_VERSIONS,
    StageBudget,
    StageError,
    stage_schema,
)
from research_workbench.analysis.stage_validation import (
    render_stage_input,
    source_coverage,
    validate_stage_output,
)


def test_map_to_document_to_cross_document_trace_and_application_ids():
    a, b = mapped(), mapped(map_request("FAKE0002"))
    da, db = reduced((a,)), reduced((b,))
    final = reduced((da, db), cross=True)
    finding = final.findings[0]
    assert finding.citations == (da.findings[0].finding_id, db.findings[0].finding_id)
    assert da.findings[0].citations == (a.findings[0].finding_id,)
    assert a.findings[0].citations == (a.request.chunks[0].chunk_id,)
    assert {c.identity for c in finding.chunks} == {
        a.request.documents[0].identity,
        b.request.documents[0].identity,
    }
    assert a.findings[0].finding_id != b.findings[0].finding_id
    assert mapped().findings == a.findings  # deterministic application IDs
    assert reduced((final, db), cross=True).findings  # same-stage hierarchical Reduce
    with pytest.raises(FrozenInstanceError):
        finding.text = "mutated"


@pytest.mark.parametrize("kind", ["source_summary", "ai_inference"])
def test_explicit_origin_and_valid_map(kind):
    request = map_request()
    result = validate_stage_output(request, output((request.chunks[0].chunk_id,), kind=kind))
    assert result.findings[0].kind == kind
    assert json.loads(result.output_json)["findings"][0]["citations"] == [
        request.chunks[0].chunk_id
    ]


def test_empty_map_and_both_reduces_are_valid_and_remain_empty():
    a, b = mapped(empty=True), mapped(map_request("FAKE0002"), empty=True)
    da, db = reduced((a,)), reduced((b,))
    final = reduced((da, db), cross=True)
    for result in (a, da, final):
        assert result.status == "no_relevant_findings" and result.findings == ()
    with pytest.raises(StageError):
        validate_stage_output(da.request, output((a.request.chunks[0].chunk_id,)))


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "bad_status",
        "empty_status",
        "nonempty_status",
        "null_findings",
        "many_findings",
        "extra_point",
        "missing_point",
        "blank",
        "long_text",
        "text_type",
        "kind",
        "category",
        "no_citations",
        "many_citations",
        "duplicate",
        "unknown",
        "ref_type",
        "surrogate",
        "model_id",
        "model_identity",
        "model_offset",
        "model_fingerprint",
    ],
)
def test_invalid_schema_and_citation_forms(mutation):
    request = map_request()
    data = json.loads(output((request.chunks[0].chunk_id,)))
    point = data["findings"][0]
    if mutation == "extra":
        data["extra"] = True
    elif mutation == "missing":
        del data["status"]
    elif mutation == "bad_status":
        data["status"] = "failed"
    elif mutation == "empty_status":
        data["findings"] = []
    elif mutation == "nonempty_status":
        data["status"] = "no_relevant_findings"
    elif mutation == "null_findings":
        data["findings"] = None
    elif mutation == "many_findings":
        data["findings"] *= 13
    elif mutation == "extra_point":
        point["extra"] = "x"
    elif mutation == "missing_point":
        del point["kind"]
    elif mutation in ("blank", "long_text", "text_type", "surrogate"):
        point["text"] = {
            "blank": " \n",
            "long_text": "x" * 2001,
            "text_type": 1,
            "surrogate": "\ud800",
        }[mutation]
    elif mutation in ("kind", "category"):
        point[mutation] = "unsupported"
    elif mutation == "no_citations":
        point["citations"] = []
    elif mutation == "many_citations":
        point["citations"] *= 21
    elif mutation == "duplicate":
        point["citations"] *= 2
    elif mutation == "unknown":
        point["citations"] = ["unknown"]
    elif mutation == "ref_type":
        point["citations"] = [True]
    else:
        point[
            {
                "model_id": "finding_id",
                "model_identity": "identity",
                "model_offset": "start",
                "model_fingerprint": "source_fingerprint",
            }[mutation]
        ] = "x"
    with pytest.raises(StageError):
        validate_stage_output(request, json.dumps(data))


@pytest.mark.parametrize(
    "raw",
    [
        '{"status":"no_relevant_findings","status":"findings","findings":[]}',
        '{"status":"no_relevant_findings","findings":[],"x":NaN}',
        '{"status":"no_relevant_findings","findings":[],"x":Infinity}',
        '{"status":"no_relevant_findings","findings":[],"x":-Infinity}',
        "```json\n{}\n```",
        "{} trailing",
        "[]",
        "null",
        "true",
        "1",
        "",
        "x" * 100001,
        "[" * 1500 + "]" * 1500,
    ],
)
def test_invalid_json_never_extracted_or_repaired(raw):
    with pytest.raises(StageError):
        validate_stage_output(map_request(), raw)


def test_nested_duplicate_key_rejected_before_mapping():
    request = map_request()
    raw = output((request.chunks[0].chunk_id,)).replace('"kind":', '"text":"hidden","kind":')
    with pytest.raises(StageError):
        validate_stage_output(request, raw)


def test_map_locality_even_for_other_chunk_of_same_current_document():
    request = map_request()
    other = chunk_document(request.documents[0], request.chunking)[1]
    with pytest.raises(StageError):
        validate_stage_output(request, output((other.chunk_id,)))


@pytest.mark.parametrize("mutation", ["old_text", "other_identity", "offset", "id", "hash", "text"])
def test_map_snapshot_chunk_integrity(mutation):
    request = map_request()
    doc, chunk = request.documents[0], request.chunks[0]
    if mutation == "old_text":
        request = replace(request, documents=(SourceDocument(doc.identity, doc.text + "changed"),))
    elif mutation == "other_identity":
        request = replace(
            request,
            documents=(
                SourceDocument(SourceIdentity("synthetic", "other-scope", "FAKE0001"), doc.text),
            ),
        )
    else:
        field = {"offset": "start", "id": "chunk_id", "hash": "content_sha256", "text": "text"}[
            mutation
        ]
        request = replace(
            request, chunks=(replace(chunk, **{field: 1 if mutation == "offset" else "x"}),)
        )
    with pytest.raises(StageError):
        render_stage_input(request)


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_child",
        "old_fingerprint",
        "missing_child",
        "duplicate_child",
        "forged_text",
        "forged_refs",
        "forged_id",
        "forged_chunks",
        "wrong_question",
        "wrong_stage",
        "cycle",
    ],
)
def test_reduce_only_accepts_current_verified_children(mutation):
    child = mapped()
    request = reduce_request((child,))
    citation = child.findings[0].finding_id
    if mutation == "foreign_child":
        request = replace(request, children=(mapped(map_request("FOREIGN")),))
    elif mutation == "old_fingerprint":
        doc = request.documents[0]
        request = replace(request, documents=(SourceDocument(doc.identity, doc.text + "new"),))
    elif mutation == "missing_child":
        citation = mapped(map_request("FOREIGN")).findings[0].finding_id
    elif mutation == "duplicate_child":
        request = replace(request, children=(child, child))
    elif mutation.startswith("forged_"):
        field, value = {
            "forged_text": ("text", "tampered"),
            "forged_refs": ("citations", ("unknown",)),
            "forged_id": ("finding_id", "model-chosen-id"),
            "forged_chunks": ("chunks", ()),
        }[mutation]
        child = replace(child, findings=(replace(child.findings[0], **{field: value}),))
        request = replace(request, children=(child,))
    elif mutation == "wrong_question":
        request = replace(request, question="Different question?")
    elif mutation == "wrong_stage":
        child = replace(child, request=replace(child.request, stage="cross_document_reduce"))
        request = replace(request, children=(child,))
    else:
        # Simulate corrupt caller-created objects, bypassing normal frozen dataclass rules.
        object.__setattr__(child, "request", request)
    with pytest.raises(StageError):
        validate_stage_output(request, output((citation,)))


def test_reduce_cannot_reference_a_grandchild_or_raw_chunk():
    child = mapped()
    doc = reduced((child,))
    request = reduce_request((doc,))
    for ref in (child.findings[0].finding_id, child.request.chunks[0].chunk_id):
        with pytest.raises(StageError):
            validate_stage_output(request, output((ref,)))


def test_overlapping_chunks_union_coverage_and_repeated_ancestors_are_not_support_counts():
    first = map_request()
    chunks = chunk_document(first.documents[0], first.chunking)
    a, b = mapped(first), mapped(replace(first, chunks=(chunks[1],)))
    result = reduced((a, b))
    assert source_coverage(result.findings[0]) == (
        (
            first.documents[0].source_fingerprint,
            ((chunks[0].start, chunks[1].end),),
        ),
    )
    # Two different intermediate conclusions can share a Map ancestor.
    other = reduced((a,))
    final = reduced((result, other))
    assert len(final.findings[0].chunks) == 2


def test_stage_dto_repr_and_safe_exceptions_hide_all_text():
    request, result = map_request(), reduced()
    for obj in (request, result, result.findings[0], result.findings[0].chunks[0]):
        assert "PRIVATE_STAGE" not in repr(obj) and "FAKE0001" not in repr(obj)
    for raw in ("PRIVATE_STAGE_PAYLOAD", output(("PRIVATE_STAGE_CITATION",))):
        with pytest.raises(StageError) as caught:
            validate_stage_output(request, raw)
        assert "PRIVATE_STAGE" not in str(caught.value)
        assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "budget",
    [
        StageBudget(output_tokens=4097),
        StageBudget(output_tokens=True),
        StageBudget(context_tokens=0),
        StageBudget(max_input_tokens=-1),
        StageBudget(safety_margin_tokens=-1),
        StageBudget(protocol_overhead_tokens=1.5),
    ],
)
def test_invalid_budget_rejected(budget):
    with pytest.raises(StageError):
        render_stage_input(replace(map_request(), budget=budget))


def test_schema_is_fresh_and_versions_are_separate():
    schemas = [stage_schema(stage, version) for stage, version in CONTRACT_VERSIONS.items()]
    assert len(set(CONTRACT_VERSIONS.values())) == 3
    for schema in schemas:
        assert schema["additionalProperties"] is False
        assert schema["properties"]["findings"]["items"]["additionalProperties"] is False
    schemas[0].clear()
    assert stage_schema("map", CONTRACT_VERSIONS["map"])["type"] == "object"


def test_maximum_findings_text_and_citations_bounds():
    request = map_request()
    data = json.loads(output((request.chunks[0].chunk_id,)))
    data["findings"][0]["text"] = "x" * 2000
    data["findings"] *= 12
    result = validate_stage_output(request, json.dumps(data))
    assert len(result.findings) == 12
    assert len({f.finding_id for f in result.findings}) == 12
    # Obtain 24 distinct application-assigned child IDs with two different map inputs.
    other_request = replace(
        request, chunks=(chunk_document(request.documents[0], request.chunking)[1],)
    )
    other_data = json.loads(output((other_request.chunks[0].chunk_id,)))
    other_data["findings"] *= 12
    other = validate_stage_output(other_request, json.dumps(other_data))
    reduce = reduce_request((result, other))
    refs = tuple(f.finding_id for child in reduce.children for f in child.findings)
    assert validate_stage_output(reduce, output(refs[:20])).findings
    with pytest.raises(StageError):
        validate_stage_output(reduce, output(refs[:21]))


def test_depth_and_graph_node_limits(monkeypatch):
    import research_workbench.analysis.stage_validation as validator

    child = mapped()
    for _ in range(16):
        child = reduced((child,))
    with pytest.raises(StageError):
        render_stage_input(reduce_request((child,)))
    # Exercise the independent traversal bound with a small configured test threshold.
    monkeypatch.setattr(validator, "MAX_GRAPH_NODES", 2)
    child = reduced((reduced((mapped(),)),))
    with pytest.raises(StageError):
        render_stage_input(reduce_request((child,)))


@pytest.mark.parametrize(
    "change", ["children", "sources", "characters", "duplicate_source", "bool_offset"]
)
def test_input_quantity_and_identity_limits(change):
    request = map_request()
    if change == "children":
        request = replace(reduce_request((mapped(),)), children=(mapped(),) * 101)
    elif change == "sources":
        request = replace(request, documents=request.documents * 101)
    elif change == "characters":
        doc = request.documents[0]
        request = replace(request, documents=(SourceDocument(doc.identity, "x" * 1_000_001),))
    elif change == "duplicate_source":
        request = replace(request, documents=request.documents * 2)
    else:
        request = replace(request, chunks=(replace(request.chunks[0], start=False),))
    with pytest.raises(StageError):
        render_stage_input(request)


def test_reduce_duplicate_citations_and_forged_provenance_numeric_types():
    child = mapped()
    request = reduce_request((child,))
    with pytest.raises(StageError):
        validate_stage_output(request, output((child.findings[0].finding_id,) * 2))
    finding = child.findings[0]
    chunk = replace(finding.chunks[0], start=False)
    forged = replace(child, findings=(replace(finding, chunks=(chunk,)),))
    with pytest.raises(StageError):
        render_stage_input(reduce_request((forged,)))


def test_reduce_input_exposes_merged_coverage_without_raw_chunk_citation_labels():
    child = mapped()
    rendered = render_stage_input(reduce_request((child,)))
    point = json.loads(rendered)["children"][0]["findings"][0]
    assert point["coverage"][0]["ranges"] == [
        [child.findings[0].chunks[0].start, child.findings[0].chunks[0].end]
    ]
    assert child.request.chunks[0].chunk_id not in rendered


def test_cross_reduce_rejects_declared_source_without_direct_child():
    a = reduced()
    b = map_request("FAKE0002").documents[0]
    request = reduce_request((a,), cross=True, documents=(*a.request.documents, b))
    # Both snapshots are current, but only A was actually supplied as a child result.
    with pytest.raises(StageError):
        validate_stage_output(request, output((a.findings[0].finding_id,)))


@pytest.mark.parametrize("empty_b", [False, True])
def test_cross_reduce_source_coverage_includes_empty_children_not_just_citations(empty_b):
    a = reduced()
    b = reduced((mapped(map_request("FAKE0002"), empty=empty_b),))
    request = reduce_request((a, b), cross=True)
    payload = json.loads(render_stage_input(request))
    assert len(payload["children"]) == 2
    assert payload["children"][1]["status"] == ("no_relevant_findings" if empty_b else "findings")
    # Source completeness concerns the INPUT. A finding need not cite every source.
    result = validate_stage_output(request, output((a.findings[0].finding_id,)))
    assert {chunk.identity for chunk in result.findings[0].chunks} == {
        a.request.documents[0].identity
    }


def test_cross_reduce_all_empty_current_sources_are_complete():
    a = reduced((mapped(empty=True),))
    b = reduced((mapped(map_request("FAKE0002"), empty=True),))
    request = reduce_request((a, b), cross=True)
    assert validate_stage_output(request, output(empty=True)).findings == ()
    missing = replace(request, children=(a,))
    with pytest.raises(StageError):
        validate_stage_output(missing, output(empty=True))


@pytest.mark.parametrize("change", ["extra", "old_version", "different_identity"])
def test_cross_reduce_rejects_mismatched_direct_source_sets(change):
    a, b = reduced(), reduced((mapped(map_request("FAKE0002")),))
    docs = (*a.request.documents, *b.request.documents)
    if change == "extra":
        children = (a, b, reduced((mapped(map_request("FAKE0003")),)))
    elif change == "old_version":
        old = mapped(map_request("FAKE0002", text="Synthetic old version."))
        children = (a, reduced((old,)))
    else:
        # Same text/key, different scope; identity includes the whole tuple.
        original = b.request.documents[0]
        foreign = SourceDocument(
            SourceIdentity("synthetic", "different-scope", original.identity.external_key),
            original.text,
        )
        req = map_request("FAKE0002")
        req = replace(req, documents=(foreign,), chunks=(chunk_document(foreign, req.chunking)[0],))
        children = (a, reduced((mapped(req),)))
    request = reduce_request(children, cross=True, documents=docs)
    for validate in (
        lambda: render_stage_input(request),
        lambda: validate_stage_output(request, output((a.findings[0].finding_id,))),
    ):
        with pytest.raises(StageError):
            validate()


def test_nested_cross_reduce_source_union_is_checked_at_each_level():
    a, b, c = (reduced((mapped(map_request(key)),)) for key in ("FAKE0001", "FAKE0002", "FAKE0003"))
    ab = reduced((a, b), cross=True)
    abc = reduced((ab, c), cross=True)
    assert {ref.identity for ref in abc.findings[0].chunks} == {
        doc.identity for doc in abc.request.documents
    }
    # Root declares A/B/C but its only direct child covers A/B.
    missing_root = replace(abc.request, children=(ab,))
    with pytest.raises(StageError):
        render_stage_input(missing_root)
    # Root union looks complete, but the nested child's own children omit B.
    incomplete_ab = replace(ab, request=replace(ab.request, children=(a,)))
    incomplete_nested = replace(abc.request, children=(incomplete_ab, c))
    with pytest.raises(StageError):
        render_stage_input(incomplete_nested)


def test_nested_document_reduce_keeps_source_coverage_for_empty_results():
    child = reduced((mapped(empty=True),))
    parent = reduced((child,))
    assert parent.status == "no_relevant_findings"
    assert render_stage_input(parent.request)
    # Even an empty nested node may not change its declared source identity/snapshot.
    foreign = map_request("FAKE0002").documents[0]
    invalid_child = replace(child, request=replace(child.request, documents=(foreign,)))
    invalid_parent = replace(parent.request, children=(invalid_child,))
    with pytest.raises(StageError):
        render_stage_input(invalid_parent)


def test_cross_reduce_overlapping_sibling_subtrees_allow_shared_sources_without_evidence_counts():
    a, b, c = (reduced((mapped(map_request(key)),)) for key in ("FAKE0001", "FAKE0002", "FAKE0003"))
    ab, bc = reduced((a, b), cross=True), reduced((b, c), cross=True)
    parent = reduced((ab, bc), cross=True)
    b_fp = b.request.documents[0].source_fingerprint
    assert len(parent.findings[0].chunks) == 3  # B's same Chunk is in the closure once.
    assert len(source_coverage(parent.findings[0])) == 3
    payload = json.loads(render_stage_input(parent.request))
    assert b_fp in payload["children"][0]["sources"]
    assert b_fp in payload["children"][1]["sources"]
    # Shared B with a conflicting snapshot fails before union/evidence dedup.
    old_b = reduced((mapped(map_request("FAKE0002", text="Synthetic old version.")),))
    old_bc = reduced((old_b, c), cross=True)
    mismatch = replace(parent.request, children=(ab, old_bc))
    with pytest.raises(StageError):
        render_stage_input(mismatch)
