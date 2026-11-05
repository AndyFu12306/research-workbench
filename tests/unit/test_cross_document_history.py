"""Scoped full-chain read integrity, corruption injection and no Zotero hydration."""

import json

import pytest
from sqlalchemy import delete, select, update
from test_cross_document_analysis import execute

from research_workbench.analysis.cross_document_pipeline import CrossDocumentError
from research_workbench.persistence import Database
from research_workbench.persistence.cross_document_models import cross_inputs, cross_stages
from research_workbench.persistence.cross_document_repository import CrossDocumentRepository
from research_workbench.persistence.document_models import chunks, inputs, stages


def test_reopen_and_detach_keep_history_without_reading_zotero(cross_setup):
    s = cross_setup
    run = execute(s)
    before = s.repo.get_history(run.id)
    for source in s.sources:
        s.links.detach(s.question.id, source.id)
    s.reader.get_fulltext = lambda _: pytest.fail("History queried Zotero")
    with Database(s.db.path) as db:
        assert CrossDocumentRepository(db).get_history(run.id) == before
    assert "a" * 100 not in repr(before)


@pytest.mark.parametrize(
    "damage",
    [
        "map_text",
        "map_finding",
        "map_status",
        "document_finding",
        "map_edge",
        "reduce_edge",
        "chunk_start",
        "chunk_hash",
        "chunk_fingerprint",
        "request_hash",
        "edge_hash",
        "cross_version",
        "cross_extra",
        "cross_execution",
        "cross_citation_execution",
        "cross_citation_ordinal",
        "cross_citation_finding",
        "cross_chunk_ordinal",
        "cross_chunk_extra",
        "cross_chunk_missing",
        "cross_chunk_duplicate",
        "cross_duplicate_key",
        "cross_bool_ordinal",
        "map_extra_field",
        "cross_wrong_type",
        "document_closure",
    ],
)
def test_full_chain_damage_is_rejected_even_when_direct_target_exists(cross_setup, damage):
    s = cross_setup
    run = execute(s)
    first = s.repo.get_members(run.id)[0]
    eid = first.document_execution_id
    with s.db._engine.begin() as c:
        if damage.startswith("chunk_"):
            field, value = {
                "chunk_start": ("start", 1),
                "chunk_hash": ("content_sha256", "f" * 64),
                "chunk_fingerprint": ("source_fingerprint", "f" * 64),
            }[damage]
            c.execute(
                update(chunks)
                .where(chunks.c.execution_id == eid, chunks.c.ordinal == 1)
                .values({field: value})
            )
        elif damage in ("map_edge", "reduce_edge"):
            ordinal = 1 if damage == "map_edge" else first.final_stage_ordinal
            c.execute(
                delete(inputs).where(
                    inputs.c.execution_id == eid, inputs.c.parent_ordinal == ordinal
                )
            )
        elif damage == "edge_hash":
            c.execute(
                update(cross_inputs)
                .where(cross_inputs.c.run_id == run.id)
                .values(child_request_sha256="f" * 64, child_result_sha256="f" * 64)
            )
        elif damage in (
            "map_text",
            "map_finding",
            "map_status",
            "document_finding",
            "request_hash",
            "map_extra_field",
            "document_closure",
        ):
            ordinal = (
                first.final_stage_ordinal
                if damage in ("document_finding", "document_closure")
                else 1
            )
            row = (
                c.execute(
                    select(stages).where(stages.c.execution_id == eid, stages.c.ordinal == ordinal)
                )
                .mappings()
                .one()
            )
            data = json.loads(row.result_json)
            if damage == "map_text":
                data["findings"][0]["text"] = "Changed intermediate finding"
            if damage in ("map_finding", "document_finding"):
                data["findings"][0]["finding_id"] = "f" * 64
            if damage == "map_extra_field":
                data["findings"][0]["extra"] = True
            if damage == "document_closure":
                data["findings"][0]["chunk_ids"] = []
            if damage == "map_status":
                data["status"] = "no_relevant_findings"
            values = (
                {"request_sha256": "f" * 64}
                if damage == "request_hash"
                else {"result_json": json.dumps(data)}
            )
            c.execute(
                update(stages)
                .where(stages.c.execution_id == eid, stages.c.ordinal == ordinal)
                .values(**values)
            )
        else:
            row = (
                c.execute(select(cross_stages).where(cross_stages.c.run_id == run.id))
                .mappings()
                .one()
            )
            data = json.loads(row.result_json)
            f = data["findings"][0]
            if damage == "cross_version":
                data["history_version"] = "unknown"
            if damage == "cross_extra":
                data["extra"] = 1
            if damage == "cross_execution":
                data["cross_execution_id"] = 999
            if damage == "cross_bool_ordinal":
                data["stage_ordinal"] = True
            if damage == "cross_citation_execution":
                f["citations"][0]["document_execution_id"] += 1
            if damage == "cross_citation_ordinal":
                f["citations"][0]["input_ordinal"] = 2
            if damage == "cross_citation_finding":
                f["citations"][0]["finding_id"] = "f" * 64
            if damage == "cross_chunk_ordinal":
                f["chunk_refs"][0]["chunk_ordinal"] += 1
            if damage == "cross_chunk_extra":
                f["chunk_refs"].append({**f["chunk_refs"][0], "chunk_id": "f" * 64})
            if damage == "cross_chunk_missing":
                f["chunk_refs"] = []
            if damage == "cross_chunk_duplicate":
                f["chunk_refs"] *= 2
            if damage == "cross_wrong_type":
                f["chunk_refs"] = None
            raw = json.dumps(data)
            if damage == "cross_duplicate_key":
                raw = raw[:-1] + ',"status":"findings"}'
            c.execute(
                update(cross_stages).where(cross_stages.c.run_id == run.id).values(result_json=raw)
            )
    with pytest.raises(CrossDocumentError) as e:
        s.repo.get_history(run.id)
    assert e.value.code == "history_integrity"


def test_same_deterministic_ids_from_another_run_do_not_supply_missing_targets(cross_setup):
    s = cross_setup
    first = execute(s)
    s.provider.calls.clear()
    second = execute(s)
    a, b = s.repo.get_members(first.id)[0], s.repo.get_members(second.id)[0]
    with s.db._engine.begin() as c:

        def payload(eid):
            return json.loads(
                c.execute(
                    select(stages.c.result_json).where(
                        stages.c.execution_id == eid, stages.c.ordinal == a.final_stage_ordinal
                    )
                ).scalar_one()
            )

        assert (
            payload(a.document_execution_id)["findings"][0]["finding_id"]
            == payload(b.document_execution_id)["findings"][0]["finding_id"]
        )
        c.execute(
            update(stages)
            .where(
                stages.c.execution_id == a.document_execution_id,
                stages.c.ordinal == a.final_stage_ordinal,
            )
            .values(result_json='{"status":"no_relevant_findings","findings":[]}')
        )
    with pytest.raises(CrossDocumentError):
        s.repo.get_history(first.id)
    assert s.repo.get_history(second.id).status == "findings"
