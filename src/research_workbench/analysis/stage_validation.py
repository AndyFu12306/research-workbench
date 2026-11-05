"""Independent schema validation and replay-checked, acyclic local provenance."""

import json

from .chunking import SourceDocument, SourceIdentity, TextChunk, chunk_document, fingerprint_parts
from .stage_contracts import (
    CATEGORIES,
    KINDS,
    MAX_CHILDREN,
    MAX_CITATIONS,
    MAX_DEPTH,
    MAX_FINDINGS,
    MAX_GRAPH_NODES,
    MAX_OUTPUT_CHARS,
    MAX_SOURCES,
    ChunkReference,
    StageBudget,
    StageError,
    StageFinding,
    StageRequest,
    ValidatedStageResult,
    stage_schema,
)


def _require(condition):
    if not condition:
        raise StageError()


def _text(value, limit):
    _require(type(value) is str and 1 <= len(value) <= limit and bool(value.strip()))
    _require(not any("\ud800" <= char <= "\udfff" for char in value))
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _constant(_value):
    raise StageError()


def _parse(raw):
    _text(raw, MAX_OUTPUT_CHARS)
    data = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    _require(type(data) is dict and set(data) == {"status", "findings"})
    status, findings = data["status"], data["findings"]
    _require(type(status) is str and status in ("findings", "no_relevant_findings"))
    _require(type(findings) is list and len(findings) <= MAX_FINDINGS)
    _require(bool(findings) == (status == "findings"))
    for finding in findings:
        _require(
            type(finding) is dict and set(finding) == {"text", "kind", "category", "citations"}
        )
        _text(finding["text"], 2000)
        _require(type(finding["kind"]) is str and finding["kind"] in KINDS)
        _require(type(finding["category"]) is str and finding["category"] in CATEGORIES)
        refs = finding["citations"]
        _require(type(refs) is list and 1 <= len(refs) <= MAX_CITATIONS)
        for ref in refs:
            _text(ref, 64)
        _require(len(set(refs)) == len(refs))
    return data


def validate_budget(budget):
    _require(type(budget) is StageBudget)
    for value in (budget.context_tokens, budget.max_input_tokens, budget.output_tokens):
        _require(type(value) is int and value > 0)
    _require(budget.output_tokens <= 4096)
    for value in (budget.safety_margin_tokens, budget.protocol_overhead_tokens):
        _require(type(value) is int and value >= 0)


class _Traversal:
    def __init__(self):
        self.active = set()
        self.nodes = 0

    def child(self, result, depth):
        _require(type(result) is ValidatedStageResult)
        self.nodes += 1
        _require(depth <= MAX_DEPTH and self.nodes <= MAX_GRAPH_NODES)
        _require(id(result) not in self.active)
        self.active.add(id(result))
        try:
            payload, local, binding = self.inputs(result.request, depth)
            expected = _build(result.request, result.output_json, local, binding)
            _require(type(result.status) is str and type(result.findings) is tuple)
            for finding in result.findings:
                _require(type(finding) is StageFinding)
                _require(type(finding.citations) is tuple and type(finding.chunks) is tuple)
                for chunk in finding.chunks:
                    _require(
                        type(chunk) is ChunkReference and type(chunk.identity) is SourceIdentity
                    )
                    _require(type(chunk.start) is int and type(chunk.end) is int)
            _require(result.status == expected.status and result.findings == expected.findings)
            return payload, binding
        finally:
            self.active.remove(id(result))

    def inputs(self, request, depth=0):
        _require(type(request) is StageRequest)
        stage_schema(request.stage, request.contract_version)
        _text(request.question, 10_000)
        validate_budget(request.budget)
        docs = request.documents
        _require(type(docs) is tuple and 1 <= len(docs) <= MAX_SOURCES)
        _require(all(type(doc) is SourceDocument for doc in docs))
        _require(sum(len(doc.text) for doc in docs) <= 1_000_000)
        for doc in docs:
            # Check the snapshot itself, including objects constructed with dataclasses.replace.
            _require(type(doc.identity) is SourceIdentity)
            _require(
                doc.identity
                == SourceIdentity(
                    doc.identity.provider,
                    doc.identity.scope,
                    doc.identity.external_key,
                )
            )
            _require(doc == SourceDocument(doc.identity, doc.text))
        current = {doc.identity: doc.source_fingerprint for doc in docs}
        _require(len(current) == len(docs))
        _require(type(request.chunks) is tuple and type(request.children) is tuple)
        local = {}
        if request.stage == "map":
            _require(len(docs) == len(request.chunks) == 1 and not request.children)
            chunk = request.chunks[0]
            _require(type(chunk) is TextChunk)
            _require(all(type(v) is int for v in (chunk.ordinal, chunk.start, chunk.end)))
            # Recompute IDs, hashes, offsets and text against the CURRENT snapshot/config.
            _require(chunk in chunk_document(docs[0], request.chunking, max_chunks=100))
            ref = ChunkReference(
                docs[0].identity,
                chunk.source_fingerprint,
                chunk.chunk_id,
                chunk.start,
                chunk.end,
                chunk.content_sha256,
            )
            local[chunk.chunk_id] = (ref,)
            material = {"chunk": {"label": chunk.chunk_id, "text": chunk.text}}
        else:
            _require(not request.chunks and 1 <= len(request.children) <= MAX_CHILDREN)
            if request.stage == "document_reduce":
                _require(len(docs) == 1)
                allowed = ("map", "document_reduce")
            else:
                _require(len(docs) >= 2)
                allowed = ("document_reduce", "cross_document_reduce")
            child_bindings, child_inputs = set(), []
            for child in request.children:
                _, binding = self.child(child, depth + 1)
                _require(child.request.stage in allowed)
                _require(child.request.question == request.question)
                _require(binding not in child_bindings)
                child_bindings.add(binding)
                for doc in child.request.documents:
                    _require(current.get(doc.identity) == doc.source_fingerprint)
                entries = []
                for finding in child.findings:
                    _require(finding.finding_id not in local)
                    local[finding.finding_id] = finding.chunks
                    entries.append(
                        {
                            "label": finding.finding_id,
                            "text": finding.text,
                            "kind": finding.kind,
                            "category": finding.category,
                            "coverage": [
                                {"source": source, "ranges": ranges}
                                for source, ranges in source_coverage(finding)
                            ],
                        }
                    )
                # Group by child to retain document boundaries without metadata or raw text.
                child_inputs.append(
                    {
                        "stage": child.request.stage,
                        "sources": [doc.source_fingerprint for doc in child.request.documents],
                        "status": child.status,
                        "findings": entries,
                    }
                )
            material = {"children": child_inputs}
        payload = {
            "stage": request.stage,
            "contract_version": request.contract_version,
            "question": request.question,
            **material,
        }
        binding = fingerprint_parts(
            [
                payload,
                [doc.source_fingerprint for doc in docs],
            ]
        )
        return payload, local, binding


def _build(request, raw, local, binding):
    data = _parse(raw)
    normalized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    findings = []
    for ordinal, item in enumerate(data["findings"]):
        chunks = {}
        for citation in item["citations"]:
            _require(citation in local)
            for chunk in local[citation]:
                # Canonical union, never an evidence count. Overlap remains explicit in offsets.
                if chunk.chunk_id in chunks:
                    _require(chunks[chunk.chunk_id] == chunk)
                chunks[chunk.chunk_id] = chunk
        _require(bool(chunks))
        finding_id = fingerprint_parts([binding, normalized, ordinal])
        findings.append(
            StageFinding(
                finding_id,
                item["text"],
                item["kind"],
                item["category"],
                tuple(item["citations"]),
                tuple(chunks.values()),
            )
        )
    return ValidatedStageResult(request, data["status"], tuple(findings), normalized)


def render_stage_input(request: StageRequest) -> str:
    """Validated actual stage input, not the 4B.1 planning envelope. Sensitive."""
    try:
        payload, _, _ = _Traversal().inputs(request)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise StageError() from None


def validate_stage_output(request: StageRequest, raw: str) -> ValidatedStageResult:
    """Assign IDs only after full validation; recursively recheck all Reduce children."""
    try:
        _, local, binding = _Traversal().inputs(request)
        return _build(request, raw, local, binding)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise StageError() from None


def source_coverage(finding: StageFinding) -> tuple[tuple[str, tuple[tuple[int, int], ...]], ...]:
    """Union of covered source intervals, not a count of independent evidence/support."""
    groups = {}
    for chunk in finding.chunks:
        groups.setdefault(chunk.source_fingerprint, []).append((chunk.start, chunk.end))
    result = []
    for source, ranges in sorted(groups.items()):
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        result.append((source, tuple(merged)))
    return tuple(result)
