"""Allowlisted Cross history and strict scoped traversal using Workbench rows only."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from research_workbench.analysis.chunking import SourceIdentity, fingerprint
from research_workbench.analysis.cross_document_pipeline import (
    HISTORY_VERSION,
    PIPELINE_VERSION,
    QUESTION_VERSION,
    CrossDocumentError,
    identity_hash,
    require,
)
from research_workbench.analysis.stage_contracts import (
    CATEGORIES,
    CONTRACT_VERSIONS,
    KINDS,
    MAX_CITATIONS,
    MAX_FINDINGS,
    PROMPT_VERSIONS,
)
from research_workbench.analysis.stage_validation import validate_stage_output

from .cross_document_models import cross_inputs, cross_stages, members, runs
from .document_models import chunks, executions, inputs, stages
from .document_repository import _json
from .source_models import SourceRow


def _hash(value):
    require(
        type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None, "history_integrity"
    )


def _int(value, minimum=1):
    require(type(value) is int and value >= minimum, "history_integrity")


def _keys(value, keys):
    require(type(value) is dict and set(value) == set(keys), "history_integrity")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "history_integrity")
        result[key] = value
    return result


def _constant(_):
    raise CrossDocumentError("history_integrity")


def _parse(raw):
    require(type(raw) is str and bool(raw), "history_integrity")
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)


def _findings(data, *, cross=False):
    require(data["status"] in ("findings", "no_relevant_findings"), "history_integrity")
    values = data["findings"]
    require(
        type(values) is list
        and len(values) <= MAX_FINDINGS
        and bool(values) == (data["status"] == "findings"),
        "history_integrity",
    )
    ids = set()
    for finding in values:
        _keys(
            finding,
            (
                "finding_id",
                "text",
                "kind",
                "category",
                "citations",
                "chunk_refs" if cross else "chunk_ids",
            ),
        )
        _hash(finding["finding_id"])
        require(finding["finding_id"] not in ids, "history_integrity")
        ids.add(finding["finding_id"])
        text = finding["text"]
        require(
            type(text) is str
            and 1 <= len(text) <= 2000
            and bool(text.strip())
            and not any("\ud800" <= c <= "\udfff" for c in text),
            "history_integrity",
        )
        require(finding["kind"] in KINDS and finding["category"] in CATEGORIES, "history_integrity")
        refs = finding["citations"]
        require(type(refs) is list and 1 <= len(refs) <= MAX_CITATIONS, "history_integrity")
        require(len({_json(c) for c in refs}) == len(refs), "history_integrity")
    return values


def document_chain_hash(session, execution_id):
    """Versioned child-result digest includes its entire persisted supporting history.

    Final result alone cannot detect an edited intermediate Map. This digest binds
    that result, plan, manifests, stages and edges; never source text or metadata.
    """
    material = []
    for table, condition, order in (
        (executions, executions.c.id == execution_id, (executions.c.id,)),
        (chunks, chunks.c.execution_id == execution_id, (chunks.c.ordinal,)),
        (stages, stages.c.execution_id == execution_id, (stages.c.ordinal,)),
        (
            inputs,
            inputs.c.execution_id == execution_id,
            (inputs.c.parent_ordinal, inputs.c.ordinal),
        ),
    ):
        rows = session.execute(select(table).where(condition).order_by(*order)).mappings()
        material.append(
            [
                {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}
                for row in rows
            ]
        )
    return fingerprint(_json(["document-history-chain-sha256-v1", *material]))


def serialize_cross_result(run_id, bindings, result):
    require(validate_stage_output(result.request, result.output_json) == result, "invalid_output")
    _int(run_id)
    require(
        result.request.stage == "cross_document_reduce"
        and 2 <= len(bindings) <= 5
        and len(bindings) == len(result.request.children)
    )
    require(
        len({b.document_execution_id for b in bindings}) == len(bindings)
        and len({b.source_id for b in bindings}) == len(bindings)
    )
    owners, chunk_owners = {}, {}
    for i, (binding, child) in enumerate(zip(bindings, result.request.children, strict=True), 1):
        for value in (
            binding.ordinal,
            binding.source_id,
            binding.document_execution_id,
            binding.final_stage_ordinal,
        ):
            _int(value)
        require(
            binding.ordinal == i
            and child.request.stage == "document_reduce"
            and binding.final_stage_ordinal == len(child.request.children) + 1
        )
        for finding in child.findings:
            _hash(finding.finding_id)
            require(finding.finding_id not in owners)
            owners[finding.finding_id] = binding
        for ordinal, m in enumerate(child.request.children, 1):
            require(
                m.request.stage == "map" and not m.request.children and len(m.request.chunks) == 1
            )
            chunk = m.request.chunks[0]
            require(chunk.ordinal == ordinal and chunk.chunk_id not in chunk_owners)
            chunk_owners[chunk.chunk_id] = {
                "input_ordinal": i,
                "document_execution_id": binding.document_execution_id,
                "chunk_ordinal": ordinal,
                "chunk_id": chunk.chunk_id,
            }
    findings = []
    for f in result.findings:
        citations = []
        for fid in f.citations:
            require(fid in owners)
            b = owners[fid]
            citations.append(
                {
                    "input_ordinal": b.ordinal,
                    "document_execution_id": b.document_execution_id,
                    "stage_ordinal": b.final_stage_ordinal,
                    "finding_id": fid,
                }
            )
        refs = []
        for chunk in f.chunks:
            require(chunk.chunk_id in chunk_owners)
            refs.append(chunk_owners[chunk.chunk_id])
        refs.sort(key=lambda r: (r["input_ordinal"], r["chunk_ordinal"]))
        findings.append(
            {
                "finding_id": f.finding_id,
                "text": f.text,
                "kind": f.kind,
                "category": f.category,
                "citations": citations,
                "chunk_refs": refs,
            }
        )
    return _json(
        {
            "history_version": HISTORY_VERSION,
            "cross_execution_id": run_id,
            "stage_ordinal": 1,
            "status": result.status,
            "findings": findings,
        }
    )


@dataclass(frozen=True, slots=True)
class CrossHistory:
    cross_execution_id: int
    stage_ordinal: int
    history_version: str
    status: str
    result_json: str = field(repr=False)
    document_execution_ids: tuple[int, ...]


def _rows(session, table, condition, *order):
    return tuple(session.execute(select(table).where(condition).order_by(*order)).mappings())


def _read_document(session, parent, member, config):
    eid = member.document_execution_id
    children = _rows(session, executions, executions.c.id == eid, executions.c.id)
    require(len(children) == 1, "history_integrity")
    run = children[0]
    require(
        run.status == "completed"
        and run.question_id == parent.question_id
        and run.source_id == member.source_id
        and run.question_sha256 == parent.question_sha256
        and run.source_fingerprint == member.source_fingerprint
        and run.content_sha256 == member.content_sha256
        and run.input_chars == member.input_chars
        and run.provider == parent.provider
        and run.model == parent.model
        and run.chunk_count + 1 == member.final_stage_ordinal,
        "history_integrity",
    )
    source = session.get(SourceRow, member.source_id)
    require(source is not None, "history_integrity")
    identity = SourceIdentity(source.provider, source.scope, source.external_key)
    require(
        identity_hash(identity) == member.identity_sha256
        and (identity.provider, identity.scope) == ("zotero", "personal"),
        "history_integrity",
    )
    expected = config["documents"][member.ordinal - 1]
    require(
        expected["identity_sha256"] == member.identity_sha256
        and expected["source_fingerprint"] == member.source_fingerprint
        and expected["content_sha256"] == member.content_sha256
        and expected["input_chars"] == member.input_chars
        and expected["read_at"] == member.read_at.isoformat(),
        "history_integrity",
    )
    child_config = _parse(run.config_json)
    require(child_config == expected["execution_config"], "history_integrity")
    require(
        child_config["pipeline_version"] == "document-pipeline-v1"
        and child_config["chunking"] == expected["chunking"]
        and child_config["planning_budget"] == expected["planning_budget"]
        and child_config["planner_estimator_version"] == expected["planner_estimator_version"]
        and child_config["planner_envelope_version"] == expected["planner_envelope_version"]
        and child_config["preflight_map_estimates"] == expected["map_estimates"]
        and child_config["stage_budget"] == config["stage_budget"]
        and child_config.get("reduce_stage_budget", child_config["stage_budget"])
        == config.get("reduce_stage_budgets", {}).get("document_reduce", config["stage_budget"])
        and child_config["pricing"] == config["pricing"],
        "history_integrity",
    )
    manifest = _rows(session, chunks, chunks.c.execution_id == eid, chunks.c.ordinal)
    rows = _rows(session, stages, stages.c.execution_id == eid, stages.c.ordinal)
    edges = _rows(
        session, inputs, inputs.c.execution_id == eid, inputs.c.parent_ordinal, inputs.c.ordinal
    )
    n = run.chunk_count
    require(len(manifest) == n and len(rows) == n + 1 and len(edges) == n * 2, "history_integrity")
    trusted, map_findings = {}, {}
    for ordinal, chunk in enumerate(manifest, 1):
        require(
            chunk.ordinal == ordinal
            and 0 <= chunk.start < chunk.end <= run.input_chars
            and chunk.source_fingerprint == run.source_fingerprint,
            "history_integrity",
        )
        for h in (chunk.chunk_id, chunk.content_sha256, chunk.source_fingerprint):
            _hash(h)
        require(
            {
                "ordinal": ordinal,
                "chunk_id": chunk.chunk_id,
                "start": chunk.start,
                "end": chunk.end,
                "content_sha256": chunk.content_sha256,
            }
            == expected["chunks"][ordinal - 1],
            "history_integrity",
        )
        trusted[chunk.chunk_id] = ordinal
    expected_edges = [(i, 1, i, None) for i in range(1, n + 1)]
    expected_edges += [(n + 1, i, None, i) for i in range(1, n + 1)]
    require(
        [(e.parent_ordinal, e.ordinal, e.chunk_ordinal, e.child_ordinal) for e in edges]
        == expected_edges,
        "history_integrity",
    )
    for ordinal, row in enumerate(rows, 1):
        stage = "map" if ordinal <= n else "document_reduce"
        require(
            row.ordinal == ordinal
            and row.stage == stage
            and row.status == "completed"
            and row.contract_version == CONTRACT_VERSIONS[stage]
            and row.prompt_version == PROMPT_VERSIONS[stage]
            and row.call_outcome == "response_received",
            "history_integrity",
        )
        _hash(row.request_sha256)
        data = _parse(row.result_json)
        _keys(data, ("status", "findings"))
        findings = _findings(data)
        for f in findings:
            ids = f["chunk_ids"]
            require(
                type(ids) is list
                and 1 <= len(ids) <= n
                and len(ids) == len(set(ids))
                and all(cid in trusted for cid in ids),
                "history_integrity",
            )
            closure = []
            for c in f["citations"]:
                if stage == "map":
                    _keys(c, ("chunk_id",))
                    require(c["chunk_id"] == manifest[ordinal - 1].chunk_id, "history_integrity")
                    closure.append(c["chunk_id"])
                else:
                    _keys(c, ("stage_ordinal", "finding_id"))
                    _int(c["stage_ordinal"])
                    _hash(c["finding_id"])
                    key = (c["stage_ordinal"], c["finding_id"])
                    require(key in map_findings, "history_integrity")
                    closure.extend(map_findings[key])
            require(ids == list(dict.fromkeys(closure)), "history_integrity")
            if stage == "map":
                map_findings[(ordinal, f["finding_id"])] = ids
    return rows[-1], {f["finding_id"]: f["chunk_ids"] for f in findings}, trusted


def read_cross_history(session, run_id):
    try:
        parent = _rows(session, runs, runs.c.id == run_id, runs.c.id)
        require(len(parent) == 1 and parent[0].status == "completed", "history_integrity")
        parent = parent[0]
        config = _parse(parent.config_json)
        require(
            parent.plan_sha256 == fingerprint(parent.config_json)
            and parent.question_format_version == QUESTION_VERSION
            and config["question_format_version"] == QUESTION_VERSION
            and config["pipeline_version"] == PIPELINE_VERSION
            and config["question_sha256"] == parent.question_sha256
            and config["provider"] == parent.provider
            and config["model"] == parent.model
            and config["contract_versions"] == CONTRACT_VERSIONS
            and config["prompt_versions"] == PROMPT_VERSIONS
            and config["child_result_fingerprint_version"] == "document-history-chain-sha256-v1",
            "history_integrity",
        )
        member_rows = _rows(session, members, members.c.run_id == run_id, members.c.ordinal)
        edges = _rows(
            session, cross_inputs, cross_inputs.c.run_id == run_id, cross_inputs.c.input_ordinal
        )
        root = _rows(session, cross_stages, cross_stages.c.run_id == run_id, cross_stages.c.ordinal)
        require(
            len(member_rows)
            == len(edges)
            == parent.document_count
            == len(config["source_ids"])
            == len(config["documents"])
            and len(root) == 1,
            "history_integrity",
        )
        stage = root[0]
        require(
            stage.ordinal == 1
            and stage.stage == "cross_document_reduce"
            and stage.status == "completed"
            and stage.prompt_version == PROMPT_VERSIONS[stage.stage]
            and stage.contract_version == CONTRACT_VERSIONS[stage.stage],
            "history_integrity",
        )
        _hash(stage.request_sha256)
        targets, chunk_ordinals = {}, {}
        for i, (member, edge) in enumerate(zip(member_rows, edges, strict=True), 1):
            require(
                member.ordinal == edge.input_ordinal == i
                and edge.parent_ordinal == 1
                and member.source_id == config["source_ids"][i - 1]
                and member.document_execution_id == edge.document_execution_id
                and member.final_stage_ordinal == edge.final_stage_ordinal,
                "history_integrity",
            )
            final, findings, chunk_ids = _read_document(session, parent, member, config)
            require(
                edge.child_request_sha256 == final.request_sha256
                and edge.child_result_sha256
                == document_chain_hash(session, member.document_execution_id),
                "history_integrity",
            )
            for fid, ids in findings.items():
                targets[(i, member.document_execution_id, member.final_stage_ordinal, fid)] = ids
            for cid, ordinal in chunk_ids.items():
                chunk_ordinals[(i, member.document_execution_id, cid)] = ordinal
        data = _parse(stage.result_json)
        _keys(
            data, ("history_version", "cross_execution_id", "stage_ordinal", "status", "findings")
        )
        _int(data["cross_execution_id"])
        _int(data["stage_ordinal"])
        require(
            data["history_version"] == HISTORY_VERSION
            and data["cross_execution_id"] == run_id
            and data["stage_ordinal"] == 1,
            "history_integrity",
        )
        for f in _findings(data, cross=True):
            closure = set()
            for c in f["citations"]:
                _keys(c, ("input_ordinal", "document_execution_id", "stage_ordinal", "finding_id"))
                for key in ("input_ordinal", "document_execution_id", "stage_ordinal"):
                    _int(c[key])
                _hash(c["finding_id"])
                key = (
                    c["input_ordinal"],
                    c["document_execution_id"],
                    c["stage_ordinal"],
                    c["finding_id"],
                )
                require(key in targets, "history_integrity")
                for cid in targets[key]:
                    closure.add((key[0], key[1], chunk_ordinals[(key[0], key[1], cid)], cid))
            expected_refs = [
                {
                    "input_ordinal": i,
                    "document_execution_id": eid,
                    "chunk_ordinal": ordinal,
                    "chunk_id": cid,
                }
                for i, eid, ordinal, cid in sorted(closure)
            ]
            refs = f["chunk_refs"]
            require(type(refs) is list, "history_integrity")
            for ref in refs:
                _keys(ref, ("input_ordinal", "document_execution_id", "chunk_ordinal", "chunk_id"))
                for key in ("input_ordinal", "document_execution_id", "chunk_ordinal"):
                    _int(ref[key])
                _hash(ref["chunk_id"])
            require(refs == expected_refs, "history_integrity")
        return CrossHistory(
            run_id,
            1,
            HISTORY_VERSION,
            data["status"],
            _json(data),
            tuple(m.document_execution_id for m in member_rows),
        )
    except Exception:
        raise CrossDocumentError("history_integrity") from None
