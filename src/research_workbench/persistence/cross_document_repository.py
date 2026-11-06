"""Short parent-owned transactions; no external reads, model calls or recovery."""

import json
import re
import secrets
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, Inexact, Rounded, localcontext

from sqlalchemy import insert, select, update
from sqlalchemy.exc import OperationalError

from research_workbench.analysis.chunking import fingerprint
from research_workbench.analysis.contracts import ProviderResult
from research_workbench.analysis.cross_document_pipeline import (
    ERRORS,
    PIPELINE_VERSION,
    QUESTION_VERSION,
    CrossConflict,
    MemberBinding,
    PreparedCross,
    _CrossExecutionHandle,
    allocate_budget,
    check_allowance,
    exact_cost,
    exact_sum,
    identity_hash,
    require,
    validate_topology,
)
from research_workbench.analysis.document_pipeline import DocumentRun, ExecutionBudget
from research_workbench.analysis.stage_contracts import CONTRACT_VERSIONS, PROMPT_VERSIONS
from research_workbench.analysis.stage_validation import render_stage_input, validate_stage_output

from .cross_document_models import cross_inputs, cross_stages, members, runs
from .document_models import executions, stages
from .document_repository import (
    DocumentAnalysisRepository,
    _json,
    _metadata,
    _pricing,
    document_config,
    history_result,
)
from .models import utc_now
from .repositories import _identifier, _pagination


@dataclass(frozen=True, slots=True)
class CrossRun:
    id: int
    question_id: int
    status: str
    provider: str
    model: str
    question_sha256: str
    question_format_version: str
    plan_sha256: str
    document_count: int
    config_json: str = field(repr=False)
    owner_sha256: str = field(repr=False)
    write_revision: int
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class CrossCosts:
    allocated_cost: Decimal | None
    known_usage_cost: Decimal | None
    usage_cost: Decimal | None
    unknown_calls: int
    not_started_calls: int


@dataclass(frozen=True, slots=True)
class CrossCallUsage:
    scope: str
    ordinal: int
    stage: str
    status: str
    call_outcome: str
    input_tokens: int | None
    output_tokens: int | None
    error_code: str | None


def plan_config(prepared):
    """Allowlisted plan identity. Never asdict a source, request or recursive result."""
    s = prepared.selection
    return {
        "pipeline_version": PIPELINE_VERSION,
        "child_result_fingerprint_version": "document-history-chain-sha256-v1",
        "question_format_version": QUESTION_VERSION,
        "question_sha256": fingerprint(s.question),
        "source_ids": list(s.source_ids),
        "provider": s.provider,
        "model": s.model,
        "contract_versions": dict(CONTRACT_VERSIONS),
        "prompt_versions": dict(PROMPT_VERSIONS),
        "stage_budget": asdict(prepared.stage_budget),
        **(
            {
                "reduce_stage_budgets": {
                    "document_reduce": asdict(prepared.budget_for("document_reduce")),
                    "cross_document_reduce": asdict(prepared.budget_for("cross_document_reduce")),
                }
            }
            if any(
                prepared.budget_for(s) != prepared.stage_budget
                for s in ("document_reduce", "cross_document_reduce")
            )
            else {}
        ),
        "budget": {
            "max_calls": prepared.budget.max_calls,
            "max_estimated_tokens": prepared.budget.max_estimated_tokens,
            "max_estimated_cost": str(prepared.budget.max_estimated_cost)
            if prepared.budget.max_estimated_cost is not None
            else None,
        },
        "pricing": None
        if prepared.pricing is None
        else {
            "currency": prepared.pricing.currency,
            "version": prepared.pricing.version,
            "input_per_million": str(prepared.pricing.input_per_million),
            "output_per_million": str(prepared.pricing.output_per_million),
        },
        "allowances": [
            [{**asdict(a), "cost": str(a.cost) if a.cost is not None else None} for a in group]
            for group in (*prepared.allocation.documents, (prepared.allocation.cross,))
        ],
        "documents": [
            {
                "identity_sha256": identity_hash(doc.identity),
                "source_fingerprint": doc.source_fingerprint,
                "content_sha256": doc.content_sha256,
                "input_chars": len(doc.text),
                "read_at": s.read_at[i].isoformat(),
                "chunking": asdict(p.plan.chunking),
                "planning_budget": asdict(p.plan.budget),
                "planner_estimator_version": p.plan.estimator_version,
                "planner_envelope_version": p.plan.calls[0].input.envelope_version,
                "chunks": [
                    {
                        "ordinal": c.input.chunk.ordinal,
                        "chunk_id": c.input.chunk.chunk_id,
                        "start": c.input.chunk.start,
                        "end": c.input.chunk.end,
                        "content_sha256": c.input.chunk.content_sha256,
                    }
                    for c in p.plan.calls
                ],
                "map_estimates": [asdict(e) for e in p.estimates],
                "execution_config": document_config(
                    p.plan,
                    prepared.stage_budget,
                    ExecutionBudget(
                        len(prepared.allocation.documents[i]),
                        sum(a.context_tokens for a in prepared.allocation.documents[i]),
                        prepared.allocation.documents[i][-1].context_tokens,
                    ),
                    prepared.pricing,
                    p.estimates,
                    reduce_stage_budget=prepared.budget_for("document_reduce"),
                ),
            }
            for i, (doc, p) in enumerate(zip(s.documents, prepared.documents, strict=True))
        ],
    }


def _safe_metadata(metadata, pricing):
    if type(metadata) is not ProviderResult:
        return {}
    input_tokens, output_tokens = (
        v if type(v) is int and v >= 0 else None
        for v in (metadata.input_tokens, metadata.output_tokens)
    )
    response_id = metadata.response_id
    if not (
        type(response_id) is str
        and len(response_id) <= 256
        and re.fullmatch(r"[A-Za-z0-9_.:/-]+", response_id)
    ):
        response_id = None
    cost = exact_cost(pricing, input_tokens, output_tokens)
    return dict(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        response_id=response_id,
        usage_cost=str(cost) if cost is not None else None,
    )


class CrossDocumentRepository:
    def __init__(self, database):
        self._database = database
        self._documents = DocumentAnalysisRepository(database)

    @contextmanager
    def _transaction(self):
        # All nominal prices, including legacy document helpers, use explicit exact precision.
        with localcontext() as context:
            context.prec = 128
            context.traps[Inexact] = context.traps[Rounded] = True
            with self._database._session() as session:
                yield session

    @staticmethod
    def _row(session, run_id):
        row = (
            session.execute(select(runs).where(runs.c.id == _identifier(run_id)))
            .mappings()
            .one_or_none()
        )
        require(row is not None)
        return row

    @staticmethod
    def _members(session, run_id):
        return tuple(
            session.execute(
                select(members).where(members.c.run_id == run_id).order_by(members.c.ordinal)
            ).mappings()
        )

    @staticmethod
    def _inputs(session, run_id):
        return tuple(
            session.execute(
                select(cross_inputs)
                .where(cross_inputs.c.run_id == run_id)
                .order_by(cross_inputs.c.input_ordinal)
            ).mappings()
        )

    @staticmethod
    def _stage(session, run_id):
        rows = tuple(
            session.execute(select(cross_stages).where(cross_stages.c.run_id == run_id)).mappings()
        )
        require(len(rows) == 1 and rows[0].ordinal == 1)
        return rows[0]

    def _lock(self, session, handle):
        if type(handle) is not _CrossExecutionHandle:
            raise CrossConflict()
        # First statement is a conditional write: serializes SQLite writers before any reads.
        try:
            changed = session.execute(
                update(runs)
                .where(
                    runs.c.id == handle.run_id,
                    runs.c.owner_sha256 == fingerprint(handle.owner),
                    runs.c.status == "running",
                )
                .values(write_revision=runs.c.write_revision + 1)
            )
        except OperationalError as exc:
            if getattr(exc.orig, "sqlite_errorcode", 0) & 255 in (5, 6):
                raise CrossConflict() from None
            raise
        if changed.rowcount != 1:
            raise CrossConflict()
        return self._row(session, handle.run_id)

    def register(self, prepared):
        require(type(prepared) is PreparedCross)
        s = prepared.selection
        s.__post_init__()
        require(len(prepared.documents) == len(s.source_ids))
        allocation = allocate_budget(
            tuple(p.estimates for p in prepared.documents),
            prepared.stage_budget,
            prepared.budget,
            prepared.pricing,
            document_reduce_budget=prepared.budget_for("document_reduce"),
            cross_reduce_budget=prepared.budget_for("cross_document_reduce"),
        )
        require(allocation == prepared.allocation)
        for p, doc in zip(prepared.documents, s.documents, strict=True):
            require(
                p.plan.documents == (doc,)
                and len(p.requests) == len(p.plan.calls)
                and all(
                    r.question == s.question
                    and r.documents == (doc,)
                    and r.stage == "map"
                    and not r.children
                    and r.budget == prepared.stage_budget
                    and r.chunking == p.plan.chunking
                    and r.chunks == (call.input.chunk,)
                    for r, call in zip(p.requests, p.plan.calls, strict=True)
                )
            )
        config = _json(plan_config(prepared))
        owner = secrets.token_hex(32)
        with self._transaction() as session:
            rid = session.execute(
                insert(runs).values(
                    question_id=s.question_id,
                    status="running",
                    provider=s.provider,
                    model=s.model,
                    question_sha256=fingerprint(s.question),
                    question_format_version=QUESTION_VERSION,
                    plan_sha256=fingerprint(config),
                    document_count=len(s.source_ids),
                    config_json=config,
                    owner_sha256=fingerprint(owner),
                    write_revision=0,
                    created_at=utc_now(),
                )
            ).inserted_primary_key[0]
            bindings = []
            for i, (p, doc, source_id) in enumerate(
                zip(prepared.documents, s.documents, s.source_ids, strict=True), 1
            ):
                group = allocation.documents[i - 1]
                budget = ExecutionBudget(
                    len(group), sum(a.context_tokens for a in group), group[-1].context_tokens
                )
                run = self._documents._create_run(
                    session,
                    s.question_id,
                    source_id,
                    p.plan,
                    provider=s.provider,
                    model=s.model,
                    stage_budget=prepared.stage_budget,
                    reduce_stage_budget=prepared.budget_for("document_reduce"),
                    execution_budget=budget,
                    pricing=prepared.pricing,
                    estimates=p.estimates,
                )
                # _create_run rechecks Question, association, identity and complete plan in T0.
                require(run.question_sha256 == fingerprint(s.question))
                binding = MemberBinding(i, source_id, run.id, len(p.requests) + 1)
                bindings.append(binding)
                session.execute(
                    insert(members).values(
                        run_id=rid,
                        **asdict(binding),
                        identity_sha256=identity_hash(doc.identity),
                        source_fingerprint=doc.source_fingerprint,
                        content_sha256=doc.content_sha256,
                        input_chars=len(doc.text),
                        read_at=s.read_at[i - 1],
                    )
                )
            session.execute(
                insert(cross_stages).values(
                    run_id=rid,
                    ordinal=1,
                    stage="cross_document_reduce",
                    status="planned",
                    prompt_version=PROMPT_VERSIONS["cross_document_reduce"],
                    contract_version=CONTRACT_VERSIONS["cross_document_reduce"],
                    call_outcome="not_started",
                    created_at=utc_now(),
                )
            )
            for b in bindings:
                session.execute(
                    insert(cross_inputs).values(
                        run_id=rid,
                        parent_ordinal=1,
                        input_ordinal=b.ordinal,
                        document_execution_id=b.document_execution_id,
                        final_stage_ordinal=b.final_stage_ordinal,
                    )
                )
            return _CrossExecutionHandle(rid, owner, tuple(bindings))

    def _registered(self, session, run, handle, prepared):
        s = prepared.selection
        config = _json(plan_config(prepared))
        require(
            run.config_json == config
            and run.plan_sha256 == fingerprint(config)
            and run.question_sha256 == fingerprint(s.question)
            and run.question_id == s.question_id
            and run.document_count == len(s.source_ids)
            and run.provider == s.provider
            and run.model == s.model
            and run.question_format_version == QUESTION_VERSION
        )
        rows, edges = self._members(session, run.id), self._inputs(session, run.id)
        require(len(rows) == len(edges) == len(s.source_ids) == len(handle.bindings))
        documents = []
        for i, (row, edge, binding, doc, p) in enumerate(
            zip(rows, edges, handle.bindings, s.documents, prepared.documents, strict=True), 1
        ):
            require(
                binding
                == MemberBinding(
                    i, s.source_ids[i - 1], row.document_execution_id, len(p.requests) + 1
                )
            )
            require(
                (row.ordinal, row.source_id, row.final_stage_ordinal)
                == (i, binding.source_id, binding.final_stage_ordinal)
                and edge.parent_ordinal == 1
                and edge.input_ordinal == i
                and edge.document_execution_id == binding.document_execution_id
                and edge.final_stage_ordinal == binding.final_stage_ordinal
                and row.identity_sha256 == identity_hash(doc.identity)
                and row.source_fingerprint == doc.source_fingerprint
                and row.content_sha256 == doc.content_sha256
                and row.input_chars == len(doc.text)
                and row.read_at == s.read_at[i - 1]
            )
            child = self._documents._run(session, binding.document_execution_id)
            require(
                child.question_id == run.question_id
                and child.source_id == binding.source_id
                and child.question_sha256 == run.question_sha256
                and child.provider == run.provider
                and child.model == run.model
                and child.chunk_count + 1 == binding.final_stage_ordinal
                and child.source_fingerprint == row.source_fingerprint
                and child.content_sha256 == row.content_sha256
                and child.input_chars == row.input_chars
            )
            cfg = json.loads(child.config_json)
            require(cfg == json.loads(config)["documents"][i - 1]["execution_config"])
            child_stages = self._documents._stages(session, child.id)
            require(len(child_stages) == child.chunk_count + 1)
            for ordinal, stage_row in enumerate(child_stages, 1):
                kind = "map" if ordinal <= child.chunk_count else "document_reduce"
                require(
                    stage_row.ordinal == ordinal
                    and stage_row.stage == kind
                    and stage_row.contract_version == CONTRACT_VERSIONS[kind]
                    and stage_row.prompt_version == PROMPT_VERSIONS[kind]
                )
            require(
                cfg["stage_budget"] == asdict(prepared.stage_budget)
                and cfg.get("reduce_stage_budget", cfg["stage_budget"])
                == asdict(prepared.budget_for("document_reduce"))
                and cfg["preflight_map_estimates"] == [asdict(e) for e in p.estimates]
                and cfg["pricing"] == json.loads(config)["pricing"]
            )
            documents.append(child)
        return tuple(documents), edges

    def start_document_stage(self, handle, prepared, member_ordinal, ordinal, request, estimate):
        with self._transaction() as session:
            run = self._lock(session, handle)
            children, _ = self._registered(session, run, handle, prepared)
            require(type(member_ordinal) is int and 1 <= member_ordinal <= len(children))
            if not all(c.status == "completed" for c in children[: member_ordinal - 1]):
                raise CrossConflict()
            child = children[member_ordinal - 1]
            if child.status != "running":
                raise CrossConflict()
            rows = self._documents._stages(session, child.id)
            if (
                type(ordinal) is not int
                or not 1 <= ordinal <= len(rows)
                or rows[ordinal - 1].status != "planned"
            ):
                raise CrossConflict()
            check_allowance(
                prepared.allocation.documents[member_ordinal - 1][ordinal - 1],
                estimate,
                prepared.budget_for(request.stage),
                prepared.pricing,
            )
            self._documents._start_stage(session, child.id, ordinal, request, estimate)

    def complete_document_stage(self, handle, prepared, member_ordinal, ordinal, result, metadata):
        with self._transaction() as session:
            run = self._lock(session, handle)
            children, _ = self._registered(session, run, handle, prepared)
            require(type(member_ordinal) is int and 1 <= member_ordinal <= len(children))
            if not all(c.status == "completed" for c in children[: member_ordinal - 1]):
                raise CrossConflict()
            child = children[member_ordinal - 1]
            rows = self._documents._stages(session, child.id)
            if (
                child.status != "running"
                or type(ordinal) is not int
                or not 1 <= ordinal <= len(rows)
                or rows[ordinal - 1].status != "running"
            ):
                raise CrossConflict()
            return self._documents._complete_stage(session, child.id, ordinal, result, metadata)

    def _bind_outcomes(self, session, run, handle, prepared, request, outcomes, *, fixed):
        validate_topology(request, prepared, outcomes)
        children, edges = self._registered(session, run, handle, prepared)
        for child, outcome, binding, edge in zip(
            children, outcomes, handle.bindings, edges, strict=True
        ):
            require(
                DocumentRun(**child) == outcome.run
                and child.status == "completed"
                and child.id == binding.document_execution_id
                and outcome.final_stage_ordinal == binding.final_stage_ordinal
            )
            result = outcome.result
            # Check every Map manifest and edge, in addition to Reduce edges.
            for n, m in enumerate((*result.request.children, result), 1):
                _, _, stored = self._documents._binding(session, child, n, m.request)
                expected = history_result(
                    m, range(1, child.chunk_count + 1) if n == binding.final_stage_ordinal else ()
                )
                require(
                    stored.status == "completed"
                    and stored.result_json == expected
                    and stored.request_sha256 == fingerprint(render_stage_input(m.request))
                    and stored.prompt_version == PROMPT_VERSIONS[m.request.stage]
                )
            request_hash = fingerprint(render_stage_input(result.request))
            from .cross_document_history import document_chain_hash

            result_hash = document_chain_hash(session, child.id)
            if fixed:
                require(
                    edge.child_request_sha256 == request_hash
                    and edge.child_result_sha256 == result_hash
                )
            else:
                require(edge.child_request_sha256 is None and edge.child_result_sha256 is None)
                session.execute(
                    update(cross_inputs)
                    .where(
                        cross_inputs.c.run_id == run.id,
                        cross_inputs.c.input_ordinal == binding.ordinal,
                    )
                    .values(child_request_sha256=request_hash, child_result_sha256=result_hash)
                )

    def start_cross(self, handle, prepared, request, outcomes, estimate):
        with self._transaction() as session:
            run = self._lock(session, handle)
            if self._stage(session, run.id).status != "planned":
                raise CrossConflict()
            self._bind_outcomes(session, run, handle, prepared, request, outcomes, fixed=False)
            cost = check_allowance(
                prepared.allocation.cross,
                estimate,
                prepared.budget_for("cross_document_reduce"),
                prepared.pricing,
            )
            changed = session.execute(
                update(cross_stages)
                .where(cross_stages.c.run_id == run.id, cross_stages.c.status == "planned")
                .values(
                    status="running",
                    started_at=utc_now(),
                    request_sha256=fingerprint(render_stage_input(request)),
                    estimated_input_tokens=estimate.input_tokens,
                    estimated_context_tokens=estimate.context_tokens,
                    estimator_version=estimate.estimator_version,
                    estimated_cost=str(cost) if cost is not None else None,
                    call_outcome="unknown",
                )
            )
            if changed.rowcount != 1:
                raise CrossConflict()

    def complete_cross(self, handle, prepared, result, outcomes, metadata):
        from .cross_document_history import serialize_cross_result

        require(
            validate_stage_output(result.request, result.output_json) == result, "invalid_output"
        )
        with self._transaction() as session:
            run = self._lock(session, handle)
            row = self._stage(session, run.id)
            if row.status != "running":
                raise CrossConflict()
            self._bind_outcomes(
                session, run, handle, prepared, result.request, outcomes, fixed=True
            )
            require(row.request_sha256 == fingerprint(render_stage_input(result.request)))
            data = serialize_cross_result(run.id, handle.bindings, result)
            values = _metadata(metadata, prepared.pricing)
            values["usage_cost"] = (
                str(exact_cost(prepared.pricing, metadata.input_tokens, metadata.output_tokens))
                if prepared.pricing
                and metadata.input_tokens is not None
                and metadata.output_tokens is not None
                else None
            )
            changed = session.execute(
                update(cross_stages)
                .where(cross_stages.c.run_id == run.id, cross_stages.c.status == "running")
                .values(
                    status="completed",
                    completed_at=utc_now(),
                    result_json=data,
                    call_outcome="response_received",
                    **values,
                )
            )
            if changed.rowcount != 1:
                raise CrossConflict()
            changed = session.execute(
                update(runs)
                .where(runs.c.id == run.id, runs.c.status == "running")
                .values(status="completed", completed_at=utc_now())
            )
            if changed.rowcount != 1:
                raise CrossConflict()
            return CrossRun(**self._row(session, run.id))

    def abort(
        self,
        handle,
        code,
        *,
        interrupted=False,
        metadata=None,
        response_received=False,
        active_document_id=None,
    ):
        """One TF, only nonterminal rows. Conflict losers must never call this method."""
        require(type(code) is str and code in ERRORS)
        terminal = "interrupted" if interrupted else "failed"
        with self._transaction() as session:
            run = self._lock(session, handle)
            now = utc_now()
            for member in self._members(session, run.id):
                child = self._documents._run(session, member.document_execution_id)
                if child.status != "running":
                    continue
                child_stages = self._documents._stages(session, child.id)
                active = any(r.status == "running" for r in child_stages)
                child_code = code if active or child.id == active_document_id else "parent_aborted"
                values = (
                    _safe_metadata(metadata, _pricing(json.loads(child.config_json)))
                    if active and metadata is not None
                    else {}
                )
                session.execute(
                    update(stages)
                    .where(stages.c.execution_id == child.id, stages.c.status == "running")
                    .values(
                        status=terminal,
                        error_code=child_code,
                        completed_at=now,
                        **values,
                        **(
                            {"call_outcome": "response_received"}
                            if metadata is not None or response_received
                            else {}
                        ),
                    )
                )
                session.execute(
                    update(stages)
                    .where(stages.c.execution_id == child.id, stages.c.status == "planned")
                    .values(status="not_started", error_code=child_code, completed_at=now)
                )
                session.execute(
                    update(executions)
                    .where(executions.c.id == child.id, executions.c.status == "running")
                    .values(
                        status=terminal if active or child.id == active_document_id else "failed",
                        error_code=child_code,
                        completed_at=now,
                    )
                )
            stage = self._stage(session, run.id)
            if stage.status in ("planned", "running"):
                values = (
                    _safe_metadata(metadata, _pricing(json.loads(run.config_json)))
                    if stage.status == "running" and metadata is not None
                    else {}
                )
                session.execute(
                    update(cross_stages)
                    .where(cross_stages.c.run_id == run.id, cross_stages.c.status == stage.status)
                    .values(
                        status=terminal if stage.status == "running" else "not_started",
                        error_code=code,
                        completed_at=now,
                        **values,
                        **(
                            {"call_outcome": "response_received"}
                            if stage.status == "running"
                            and (metadata is not None or response_received)
                            else {}
                        ),
                    )
                )
            session.execute(
                update(runs)
                .where(runs.c.id == run.id, runs.c.status == "running")
                .values(status=terminal, error_code=code, completed_at=now)
            )

    def get(self, run_id):
        with self._database._session() as session:
            row = (
                session.execute(select(runs).where(runs.c.id == _identifier(run_id)))
                .mappings()
                .one_or_none()
            )
            return CrossRun(**row) if row else None

    def list_by_question(self, question_id, *, limit=100, offset=0):
        _pagination(limit, offset)
        with self._database._session() as session:
            return tuple(
                CrossRun(**row)
                for row in session.execute(
                    select(runs)
                    .where(runs.c.question_id == _identifier(question_id))
                    .order_by(runs.c.id)
                    .limit(limit)
                    .offset(offset)
                ).mappings()
            )

    def get_members(self, run_id):
        with self._database._session() as session:
            return tuple(
                MemberBinding(
                    r.ordinal, r.source_id, r.document_execution_id, r.final_stage_ordinal
                )
                for r in self._members(session, _identifier(run_id))
            )

    def get_history(self, run_id):
        from .cross_document_history import read_cross_history

        with self._database._session() as session:
            return read_cross_history(session, _identifier(run_id))

    def get_costs(self, run_id):
        with self._database._session() as session:
            run = self._row(session, run_id)
            config = json.loads(run.config_json)
            rows = [
                r
                for m in self._members(session, run_id)
                for r in self._documents._stages(session, m.document_execution_id)
            ]
            rows.append(self._stage(session, run_id))
            known = exact_sum(Decimal(r.usage_cost) for r in rows if r.usage_cost is not None)
            if config["pricing"] is None:
                known = None
            total = (
                known
                if all(
                    r.call_outcome == "response_received" and r.usage_cost is not None for r in rows
                )
                else None
            )
            allocated = (
                exact_sum(Decimal(a["cost"]) for g in config["allowances"] for a in g)
                if config["pricing"]
                else None
            )
            return CrossCosts(
                allocated,
                known,
                total,
                sum(r.call_outcome == "unknown" for r in rows),
                sum(r.call_outcome == "not_started" for r in rows),
            )

    def get_call_usage(self, run_id):
        """Offline usage/status projection, including failed and unstarted calls."""
        with self._database._session() as session:
            self._row(session, _identifier(run_id))
            values = [
                (f"document-{m.ordinal}", r)
                for m in self._members(session, run_id)
                for r in self._documents._stages(session, m.document_execution_id)
            ]
            values.append(("cross", self._stage(session, run_id)))
            return tuple(
                CrossCallUsage(
                    scope,
                    r.ordinal,
                    r.stage,
                    r.status,
                    r.call_outcome,
                    r.input_tokens,
                    r.output_tokens,
                    r.error_code,
                )
                for scope, r in values
            )

    def has_failed_candidate(self, run_id):
        """Boolean projection only; do not retrieve failed candidate text."""
        with self._database._session() as session:
            self._row(session, _identifier(run_id))
            ids = [m.document_execution_id for m in self._members(session, run_id)]
            document_values = session.execute(
                select(stages.c.result_json.is_not(None)).where(
                    stages.c.execution_id.in_(ids), stages.c.status.in_(("failed", "interrupted"))
                )
            ).scalars()
            cross_values = session.execute(
                select(cross_stages.c.result_json.is_not(None)).where(
                    cross_stages.c.run_id == run_id,
                    cross_stages.c.status.in_(("failed", "interrupted")),
                )
            ).scalars()
            return any(document_values) or any(cross_values)
