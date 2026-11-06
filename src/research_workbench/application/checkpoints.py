"""Offline checkpoint assessment; never issue provider calls or mutate historical rows."""

import json
from dataclasses import asdict, dataclass

from research_workbench.analysis.chunking import fingerprint
from research_workbench.analysis.stage_contracts import CONTRACT_VERSIONS, PROMPT_VERSIONS
from research_workbench.analysis.stage_validation import render_stage_input, validate_stage_output
from research_workbench.persistence.cross_document_repository import CrossDocumentRepository
from research_workbench.persistence.document_repository import history_result


class CheckpointUnavailable(ValueError):
    """A fixed refusal; no historical text or source values in errors."""

    def __init__(self):
        super().__init__(
            "Checkpoint cannot be reconstructed and verified losslessly; no request sent."
        )


def replay_stage_projection(request, run, stage, *, provider, model):
    """Conditionally reuse an old projection as a verified in-memory child result.

    Caller must supply freshly frozen source/question inputs. Canonical JSON ordering was
    not stored, so reconstruction is accepted ONLY when every original ID/projection matches.
    This primitive does not register executions, replay paid calls or authorize a resume.
    """
    try:
        config = json.loads(run.config_json)
        expected_budget = (
            config.get("reduce_stage_budget", config["stage_budget"])
            if request.stage == "document_reduce"
            else config["stage_budget"]
        )
        doc = request.documents[0]
        if not (
            request.stage in ("map", "document_reduce")
            and stage.execution_id == run.id
            and run.status in ("completed", "failed", "interrupted")
            and stage.status == "completed"
            and stage.call_outcome == "response_received"
            and (run.provider, run.model) == (provider, model)
            and stage.stage == request.stage
            and stage.contract_version
            == request.contract_version
            == CONTRACT_VERSIONS[request.stage]
            and stage.prompt_version == PROMPT_VERSIONS[request.stage]
            and fingerprint(request.question) == run.question_sha256
            and doc.source_fingerprint == run.source_fingerprint
            and doc.content_sha256 == run.content_sha256
            and len(doc.text) == run.input_chars
            and asdict(request.budget) == expected_budget
            and asdict(request.chunking) == config["chunking"]
            and fingerprint(render_stage_input(request)) == stage.request_sha256
            and stage.ordinal
            == (request.chunks[0].ordinal if request.stage == "map" else run.chunk_count + 1)
        ):
            raise CheckpointUnavailable()
        projected = json.loads(stage.result_json)
        raw = json.dumps(
            {
                "status": projected["status"],
                "findings": [
                    {
                        "text": f["text"],
                        "kind": f["kind"],
                        "category": f["category"],
                        "citations": [
                            c["chunk_id"] if request.stage == "map" else c["finding_id"]
                            for c in f["citations"]
                        ],
                    }
                    for f in projected["findings"]
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = validate_stage_output(request, raw)
        ordinals = range(1, len(request.children) + 1) if request.stage == "document_reduce" else ()
        if history_result(result, ordinals) != stage.result_json:
            raise CheckpointUnavailable()
        return result
    except Exception:
        raise CheckpointUnavailable() from None


@dataclass(frozen=True, slots=True)
class ExecutionInspection:
    completed: int
    failed: int
    not_started: int
    raw_failed_candidate_saved: bool
    diagnostic_available: bool
    checkpoint_resume_available: bool = False


def inspect_execution(database, execution_id):
    """Counts/status only; never load any original content or provider candidates."""
    repo = CrossDocumentRepository(database)
    run = repo.get(execution_id)
    if run is None:
        raise CheckpointUnavailable()
    calls = repo.get_call_usage(execution_id)
    return ExecutionInspection(
        sum(c.status == "completed" for c in calls),
        sum(c.status in ("failed", "interrupted") for c in calls),
        sum(c.call_outcome == "not_started" for c in calls),
        repo.has_failed_candidate(execution_id),
        any((c.error_code or "").startswith("invalid_output_") for c in calls),
    )
