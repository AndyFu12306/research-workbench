"""Atomic checkpoint storage and stable digest of scoped historical execution rows."""

import json

from sqlalchemy import insert, select

from research_workbench.analysis.chunking import fingerprint
from research_workbench.analysis.stage_validation import render_stage_input, validate_stage_output

from .checkpoint_models import checkpoints
from .cross_document_models import cross_inputs, cross_stages, members, runs
from .document_models import chunks, executions, inputs, stages


def save_checkpoint(session, run_id, member, ordinal, result, projection, source_run_id=None):
    if validate_stage_output(result.request, result.output_json) != result:
        raise ValueError("Invalid checkpoint")
    session.execute(
        insert(checkpoints).values(
            run_id=run_id,
            member=member,
            ordinal=ordinal,
            output_json=result.output_json,
            output_sha256=fingerprint(result.output_json),
            request_sha256=fingerprint(render_stage_input(result.request)),
            projection_sha256=fingerprint(projection),
            origin="api" if source_run_id is None else "reuse",
            source_run_id=source_run_id,
        )
    )


def execution_digest(session, run_id):
    """No source payloads; bind all persisted support rows, ordering and checkpoints."""
    parts = []
    for table, predicate in (
        (runs, runs.c.id == run_id),
        (members, members.c.run_id == run_id),
        (cross_stages, cross_stages.c.run_id == run_id),
        (cross_inputs, cross_inputs.c.run_id == run_id),
        (checkpoints, checkpoints.c.run_id == run_id),
    ):
        rows = session.execute(
            select(table).where(predicate).order_by(*table.primary_key)
        ).mappings()
        parts.append([dict(r) for r in rows])
    ids = select(members.c.document_execution_id).where(members.c.run_id == run_id)
    for table, key in (
        (executions, executions.c.id),
        (chunks, chunks.c.execution_id),
        (stages, stages.c.execution_id),
        (inputs, inputs.c.execution_id),
    ):
        rows = session.execute(
            select(table).where(key.in_(ids)).order_by(*table.primary_key)
        ).mappings()
        parts.append([dict(r) for r in rows])
    return fingerprint(json.dumps(parts, default=lambda v: v.isoformat(), separators=(",", ":")))
