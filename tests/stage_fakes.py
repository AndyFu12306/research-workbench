"""Synthetic stage builders; never read Zotero, user databases or credentials."""

import json

from research_workbench.analysis.chunking import (
    ChunkingConfig,
    SourceDocument,
    SourceIdentity,
    chunk_document,
)
from research_workbench.analysis.stage_contracts import CONTRACT_VERSIONS, StageRequest
from research_workbench.analysis.stage_validation import validate_stage_output


def map_request(
    key="FAKE0001", text="PRIVATE_STAGE_SOURCE 中文\n\nSynthetic method and limitation."
):
    doc = SourceDocument(SourceIdentity("synthetic", "example", key), text)
    config = ChunkingConfig(max_chars=40, overlap_chars=5)
    return StageRequest(
        "map",
        CONTRACT_VERSIONS["map"],
        "PRIVATE_STAGE_QUESTION Compare methods?",
        (doc,),
        chunks=(chunk_document(doc, config)[0],),
        chunking=config,
    )


def output(citations=(), *, empty=False, kind="source_summary"):
    return json.dumps(
        {
            "status": "no_relevant_findings" if empty else "findings",
            "findings": []
            if empty
            else [
                {
                    "text": "PRIVATE_STAGE_FINDING Synthetic result with uncertainty.",
                    "kind": kind,
                    "category": "finding",
                    "citations": list(citations),
                }
            ],
        }
    )


def mapped(request=None, *, empty=False):
    request = request or map_request()
    return validate_stage_output(request, output((request.chunks[0].chunk_id,), empty=empty))


def reduce_request(children, *, cross=False, documents=None):
    stage = "cross_document_reduce" if cross else "document_reduce"
    docs = {}
    for child in children:
        for doc in child.request.documents:
            docs[doc.identity] = doc
    return StageRequest(
        stage,
        CONTRACT_VERSIONS[stage],
        children[0].request.question,
        documents if documents is not None else tuple(docs.values()),
        children=tuple(children),
    )


def reduced(children=None, *, cross=False):
    children = children or (mapped(),)
    request = reduce_request(children, cross=cross)
    refs = tuple(f.finding_id for child in children for f in child.findings)
    return validate_stage_output(request, output(refs, empty=not refs))
