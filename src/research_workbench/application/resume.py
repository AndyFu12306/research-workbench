"""Fail-closed, explicit resume of complete canonical checkpoints; no provider calls."""

import json
from dataclasses import dataclass, field, replace
from decimal import Decimal

from sqlalchemy import select

from research_workbench.analysis.chunking import ChunkingConfig, fingerprint
from research_workbench.analysis.cross_document_pipeline import (
    CrossBudget,
    MemberBinding,
    _CrossExecutionHandle,
    check_allowance,
    exact_sum,
)
from research_workbench.analysis.planning import PlanningBudget
from research_workbench.analysis.stage_contracts import (
    CONTRACT_VERSIONS,
    PROMPT_VERSIONS,
    StageBudget,
    StageRequest,
    ValidatedStageResult,
)
from research_workbench.analysis.stage_validation import render_stage_input, validate_stage_output
from research_workbench.persistence.checkpoint_models import checkpoints, resumes
from research_workbench.persistence.checkpoints import execution_digest
from research_workbench.persistence.cross_document_repository import plan_config
from research_workbench.persistence.document_repository import _pricing, history_result

from .checkpoints import CheckpointUnavailable, inspect_execution
from .cross_document_analysis import CrossDocumentAnalysisService


@dataclass(frozen=True)
class ResumePlan:
    source_id: int
    source_sha256: str
    results: tuple[tuple[int, int, ValidatedStageResult], ...] = field(repr=False)
    new_calls: int
    new_context_units: int
    new_cost: Decimal | None


def service_for_resume(database, reader, provider, source_id):
    """Restore configuration, never credentials or content, from the original plan."""
    from research_workbench.persistence.cross_document_repository import CrossDocumentRepository

    repo = CrossDocumentRepository(database)
    run = repo.get(source_id)
    if run is None or not inspect_execution(database, source_id).checkpoint_resume_available:
        raise CheckpointUnavailable()
    if (run.provider, run.model) != (provider.provider, provider.model):
        raise CheckpointUnavailable()
    config = json.loads(run.config_json)
    budget = dict(config["budget"])
    if budget["max_estimated_cost"] is not None:
        budget["max_estimated_cost"] = Decimal(budget["max_estimated_cost"])
    stages = config.get("reduce_stage_budgets", {})
    service = CrossDocumentAnalysisService(
        database,
        reader,
        provider,
        chunking=ChunkingConfig(**config["documents"][0]["chunking"]),
        planning_budget=PlanningBudget(**config["documents"][0]["planning_budget"]),
        stage_budget=StageBudget(**config["stage_budget"]),
        document_reduce_budget=StageBudget(**stages.get("document_reduce", config["stage_budget"])),
        cross_reduce_budget=StageBudget(
            **stages.get("cross_document_reduce", config["stage_budget"])
        ),
        cross_budget=CrossBudget(**budget),
        pricing=_pricing(config),
    )
    prepared = service.prepare_analysis(run.question_id, tuple(config["source_ids"]))
    return service, prepared, verify_resume(service, source_id, prepared)


def verify_resume(service, source_id, prepared):
    """Revalidate every success and original ID against fresh question/source snapshots."""
    try:
        repo, docs = service._runs, service._selection
        old = repo.get(source_id)
        if old.status not in ("failed", "interrupted"):
            raise CheckpointUnavailable()
        config, expected = json.loads(old.config_json), plan_config(prepared)
        if fingerprint(old.config_json) != old.plan_sha256:
            raise CheckpointUnavailable()
        # Read timestamps describe the new read; every other setting and fingerprint must match.
        for d in config["documents"]:
            d.pop("read_at")
        for d in expected["documents"]:
            d.pop("read_at")
        if config != expected:
            raise CheckpointUnavailable()
        results, reused_allowances = [], []
        with repo._database._session() as session:
            if session.execute(select(resumes).where(resumes.c.source_run_id == source_id)).first():
                raise CheckpointUnavailable()
            rows = list(
                session.execute(
                    select(checkpoints).where(checkpoints.c.run_id == source_id)
                ).mappings()
            )
            saved = {(r.member, r.ordinal): r for r in rows}
            bindings = repo._members(session, source_id)
            if len(bindings) != len(prepared.documents):
                raise CheckpointUnavailable()
            historical_prepared = replace(
                prepared,
                selection=replace(prepared.selection, read_at=tuple(m.read_at for m in bindings)),
            )
            repo._registered(
                session,
                old,
                _CrossExecutionHandle(
                    source_id,
                    "",
                    tuple(
                        MemberBinding(
                            m.ordinal, m.source_id, m.document_execution_id, m.final_stage_ordinal
                        )
                        for m in bindings
                    ),
                ),
                historical_prepared,
            )
            ended = False
            for m, p in zip(bindings, prepared.documents, strict=True):
                run = docs._run(session, m.document_execution_id)
                stages = docs._stages(session, run.id)
                if run.config_json != json.dumps(
                    expected["documents"][m.ordinal - 1]["execution_config"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ):
                    raise CheckpointUnavailable()
                if (run.provider, run.model, run.source_id, run.question_id) != (
                    old.provider,
                    old.model,
                    m.source_id,
                    old.question_id,
                ) or run.source_id != prepared.selection.source_ids[m.ordinal - 1]:
                    raise CheckpointUnavailable()
                if len(stages) != len(p.requests) + 1:
                    raise CheckpointUnavailable()
                children = []
                for n, stage in enumerate(stages, 1):
                    kind = "map" if n <= len(p.requests) else "document_reduce"
                    if (
                        stage.ordinal != n
                        or stage.stage != kind
                        or stage.prompt_version != PROMPT_VERSIONS[kind]
                        or stage.contract_version != CONTRACT_VERSIONS[kind]
                    ):
                        raise CheckpointUnavailable()
                    if stage.status != "completed":
                        ended = True
                        if stage.status not in ("failed", "interrupted", "not_started"):
                            raise CheckpointUnavailable()
                        if kind == "document_reduce" and len(children) == len(p.requests):
                            ready = StageRequest(
                                kind,
                                CONTRACT_VERSIONS[kind],
                                prepared.selection.question,
                                (prepared.selection.documents[m.ordinal - 1],),
                                budget=prepared.budget_for(kind),
                                chunking=p.plan.chunking,
                                children=tuple(children),
                            )
                            check_allowance(
                                prepared.allocation.documents[m.ordinal - 1][-1],
                                service._documents._estimate(ready),
                                ready.budget,
                                prepared.pricing,
                            )
                        continue
                    if ended:
                        raise CheckpointUnavailable()
                    request = (
                        p.requests[n - 1]
                        if kind == "map"
                        else StageRequest(
                            kind,
                            CONTRACT_VERSIONS[kind],
                            prepared.selection.question,
                            (prepared.selection.documents[m.ordinal - 1],),
                            budget=prepared.budget_for(kind),
                            chunking=p.plan.chunking,
                            children=tuple(children),
                        )
                    )
                    docs._binding(session, run, n, request)
                    cp = saved[(m.ordinal, n)]
                    if cp.origin == "api":
                        if cp.source_run_id is not None:
                            raise CheckpointUnavailable()
                    elif cp.origin == "reuse":
                        lineage = (
                            session.execute(select(resumes).where(resumes.c.run_id == source_id))
                            .mappings()
                            .one()
                        )
                        if (
                            cp.source_run_id != lineage.source_run_id
                            or cp.source_run_id >= source_id
                        ):
                            raise CheckpointUnavailable()
                        original = (
                            session.execute(
                                select(checkpoints).where(
                                    checkpoints.c.run_id == cp.source_run_id,
                                    checkpoints.c.member == m.ordinal,
                                    checkpoints.c.ordinal == n,
                                )
                            )
                            .mappings()
                            .one()
                        )
                        if any(
                            cp[k] != original[k]
                            for k in (
                                "output_json",
                                "output_sha256",
                                "request_sha256",
                                "projection_sha256",
                            )
                        ):
                            raise CheckpointUnavailable()
                    if not (
                        stage.call_outcome == "response_received"
                        and cp.origin in ("api", "reuse")
                        and fingerprint(cp.output_json) == cp.output_sha256
                        and fingerprint(stage.result_json) == cp.projection_sha256
                        and fingerprint(render_stage_input(request))
                        == cp.request_sha256
                        == stage.request_sha256
                    ):
                        raise CheckpointUnavailable()
                    result = validate_stage_output(request, cp.output_json)
                    if (
                        result.output_json != cp.output_json
                        or history_result(result, range(1, n) if kind == "document_reduce" else ())
                        != stage.result_json
                    ):
                        raise CheckpointUnavailable()
                    # Recheck the actual reconstructed input/context; never send it here.
                    service._documents._estimate(request)
                    results.append((m.ordinal, n, result))
                    reused_allowances.append(prepared.allocation.documents[m.ordinal - 1][n - 1])
                    if kind == "map":
                        children.append(result)
            if set(saved) != {(m, n) for m, n, _ in results}:
                raise CheckpointUnavailable()
            reductions = tuple(r for _, _, r in results if r.request.stage == "document_reduce")
            if len(reductions) == len(prepared.documents):
                ready = StageRequest(
                    "cross_document_reduce",
                    CONTRACT_VERSIONS["cross_document_reduce"],
                    prepared.selection.question,
                    prepared.selection.documents,
                    budget=prepared.budget_for("cross_document_reduce"),
                    chunking=prepared.documents[0].plan.chunking,
                    children=reductions,
                )
                check_allowance(
                    prepared.allocation.cross,
                    service._documents._estimate(ready),
                    ready.budget,
                    prepared.pricing,
                )
            digest = execution_digest(session, source_id)
        remaining_calls = prepared.allocation.calls - len(results)
        remaining_context = prepared.allocation.context_tokens - sum(
            a.context_tokens for a in reused_allowances
        )
        used = {(m, n) for m, n, _ in results}
        remaining_allowances = [
            a
            for m, group in enumerate(prepared.allocation.documents, 1)
            for n, a in enumerate(group, 1)
            if (m, n) not in used
        ]
        remaining_allowances.append(prepared.allocation.cross)
        remaining_cost = (
            exact_sum(a.cost for a in remaining_allowances) if prepared.pricing else None
        )
        if (
            remaining_calls > prepared.budget.max_calls
            or remaining_context > prepared.budget.max_estimated_tokens
        ):
            raise CheckpointUnavailable()
        if (
            prepared.budget.max_estimated_cost is not None
            and remaining_cost > prepared.budget.max_estimated_cost
        ):
            raise CheckpointUnavailable()
        return ResumePlan(
            source_id, digest, tuple(results), remaining_calls, remaining_context, remaining_cost
        )
    except Exception:
        raise CheckpointUnavailable() from None
