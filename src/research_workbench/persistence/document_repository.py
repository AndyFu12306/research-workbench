"""Scoped document history; short transactions and no external I/O."""

import json
import re
from dataclasses import asdict

from sqlalchemy import insert, inspect, select, update

from research_workbench.analysis.chunking import SourceIdentity, chunk_document, fingerprint
from research_workbench.analysis.contracts import ProviderResult
from research_workbench.analysis.document_pipeline import (
    PIPELINE_ERRORS,
    ChunkManifest,
    DocumentAnalysisError,
    DocumentRun,
    ExecutionBudget,
    Pricing,
    RequestEstimate,
    StageInput,
    StageRun,
    render_document_question,
)
from research_workbench.analysis.planning import MapPlan
from research_workbench.analysis.stage_contracts import (
    CONTRACT_VERSIONS,
    PROMPT_VERSIONS,
    StageBudget,
    StageRequest,
    ValidatedStageResult,
)
from research_workbench.analysis.stage_validation import render_stage_input, validate_stage_output
from research_workbench.domain import Question, SourceRef

from .database import Database
from .document_models import chunks, executions, inputs, stages
from .models import QuestionRow, utc_now
from .repositories import _identifier, _pagination, _question
from .source_models import QuestionSourceRow, SourceRow
from .source_repositories import _reference


def _require(condition, code="invalid_plan"):
    if not condition:
        raise DocumentAnalysisError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _name(value, maximum):
    _require(
        type(value) is str
        and re.fullmatch(r"[A-Za-z0-9_.:/-]+", value) is not None
        and len(value) <= maximum
    )
    return value


def _metadata(result, pricing):
    if result is None:
        return {}
    _require(type(result) is ProviderResult, "invalid_output")
    for v in (result.input_tokens, result.output_tokens):
        _require(v is None or (type(v) is int and v >= 0), "invalid_output")
    if result.response_id is not None:
        _name(result.response_id, 256)
    cost = pricing.cost(result.input_tokens, result.output_tokens) if pricing else None
    return dict(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        response_id=result.response_id,
        usage_cost=str(cost) if cost is not None else None,
    )


def _pricing(config):
    from decimal import Decimal

    value = config["pricing"]
    return (
        Pricing(
            value["currency"],
            value["version"],
            Decimal(value["input_per_million"]),
            Decimal(value["output_per_million"]),
        )
        if value
        else None
    )


def history_result(result, child_ordinals=()):
    """Explicit allowlist, never serialize request/documents/raw ProviderResult."""
    owners = {
        f.finding_id: ordinal
        for ordinal, child in zip(child_ordinals, result.request.children, strict=True)
        for f in child.findings
    }
    return _json(
        {
            "status": result.status,
            "findings": [
                {
                    "finding_id": f.finding_id,
                    "text": f.text,
                    "kind": f.kind,
                    "category": f.category,
                    "citations": (
                        [{"chunk_id": c} for c in f.citations]
                        if result.request.stage == "map"
                        else [{"stage_ordinal": owners[c], "finding_id": c} for c in f.citations]
                    ),
                    "chunk_ids": [c.chunk_id for c in f.chunks],
                }
                for f in result.findings
            ],
        }
    )


def document_config(
    plan, stage_budget, execution_budget, pricing, estimates, *, reduce_stage_budget=None
):
    price_dict = (
        None
        if pricing is None
        else {
            **asdict(pricing),
            "input_per_million": str(pricing.input_per_million),
            "output_per_million": str(pricing.output_per_million),
        }
    )
    config = {
        "pipeline_version": "document-pipeline-v1",
        "request_fingerprint_version": "stage-input-sha256-v1",
        "input_kind": "fulltext",
        "chunking_version": plan.calls[0].input.chunk.algorithm_version,
        "chunking": asdict(plan.chunking),
        "planner_estimator_version": plan.estimator_version,
        "planner_envelope_version": plan.calls[0].input.envelope_version,
        "planning_budget": asdict(plan.budget),
        "planner_estimated_tokens": plan.total_estimated_tokens,
        "preflight_map_estimates": [asdict(estimate) for estimate in estimates],
        "stage_budget": asdict(stage_budget),
        "execution_budget": {
            **asdict(execution_budget),
            "max_estimated_cost": str(execution_budget.max_estimated_cost)
            if execution_budget.max_estimated_cost is not None
            else None,
        },
        "pricing": price_dict,
    }
    if reduce_stage_budget is not None and reduce_stage_budget != stage_budget:
        config["reduce_stage_budget"] = asdict(reduce_stage_budget)
    return config


class DocumentAnalysisRepository:
    def __init__(self, database: Database):
        self._database = database

    def get_selection(self, question_id: int, source_id: int) -> tuple[Question, SourceRef]:
        """Resolve exactly one current association, with no pagination or hydration."""
        with self._database._session() as session:
            question = session.get(QuestionRow, _identifier(question_id))
            source = session.get(SourceRow, _identifier(source_id))
            _require(
                question is not None
                and source is not None
                and session.get(QuestionSourceRow, (question_id, source_id)) is not None,
                "source_unavailable",
            )
            return _question(question), _reference(source)

    @staticmethod
    def _run(session, execution_id, *, running=False):
        row = (
            session.execute(select(executions).where(executions.c.id == _identifier(execution_id)))
            .mappings()
            .one_or_none()
        )
        _require(row is not None)
        if running:
            _require(row.status == "running", "immutable_run")
        return row

    @staticmethod
    def _stages(session, execution_id):
        return tuple(
            session.execute(
                select(stages)
                .where(stages.c.execution_id == execution_id)
                .order_by(stages.c.ordinal)
            ).mappings()
        )

    def get(self, execution_id: int) -> DocumentRun | None:
        with self._database._session() as session:
            row = (
                session.execute(
                    select(executions).where(executions.c.id == _identifier(execution_id))
                )
                .mappings()
                .one_or_none()
            )
            return DocumentRun(**row) if row else None

    def list_by_question(self, question_id: int, *, limit=100, offset=0) -> tuple[DocumentRun, ...]:
        _pagination(limit, offset)
        with self._database._session() as session:
            return tuple(
                DocumentRun(**r)
                for r in session.execute(
                    select(executions)
                    .where(executions.c.question_id == _identifier(question_id))
                    .order_by(executions.c.id)
                    .limit(limit)
                    .offset(offset)
                ).mappings()
            )

    def get_chunks(self, execution_id: int) -> tuple[ChunkManifest, ...]:
        with self._database._session() as session:
            return tuple(
                ChunkManifest(**r)
                for r in session.execute(
                    select(chunks)
                    .where(chunks.c.execution_id == _identifier(execution_id))
                    .order_by(chunks.c.ordinal)
                ).mappings()
            )

    def get_stages(self, execution_id: int) -> tuple[StageRun, ...]:
        with self._database._session() as session:
            from .checkpoint_models import checkpoints
            from .cross_document_models import members

            reused = set()
            if inspect(session.connection()).has_table("stage_checkpoints"):
                reused = set(
                    session.execute(
                        select(checkpoints.c.ordinal)
                        .join(
                            members,
                            (members.c.run_id == checkpoints.c.run_id)
                            & (members.c.ordinal == checkpoints.c.member),
                        )
                        .where(
                            members.c.document_execution_id == execution_id,
                            checkpoints.c.origin == "reuse",
                        )
                    ).scalars()
                )
            return tuple(
                StageRun(
                    **{
                        **r,
                        "call_outcome": "reused" if r.ordinal in reused else r.call_outcome,
                    }
                )
                for r in self._stages(session, _identifier(execution_id))
            )

    def get_inputs(self, execution_id: int) -> tuple[StageInput, ...]:
        with self._database._session() as session:
            return tuple(
                StageInput(**r)
                for r in session.execute(
                    select(inputs)
                    .where(inputs.c.execution_id == _identifier(execution_id))
                    .order_by(inputs.c.parent_ordinal, inputs.c.ordinal)
                ).mappings()
            )

    def create_run(self, *args, **kwargs):
        with self._database._session() as session:
            return self._create_run(session, *args, **kwargs)

    def _create_run(
        self,
        session,
        question_id: int,
        source_id: int,
        plan: MapPlan,
        *,
        provider: str,
        model: str,
        stage_budget: StageBudget,
        execution_budget: ExecutionBudget,
        pricing: Pricing | None,
        estimates: tuple[RequestEstimate, ...],
        reduce_stage_budget: StageBudget | None = None,
    ) -> DocumentRun:
        _name(provider, 64)
        _name(model, 256)
        _require(len(plan.documents) == 1 and len(estimates) == len(plan.calls))
        for estimate in estimates:
            _require(type(estimate) is RequestEstimate)
            _name(estimate.estimator_version, 64)
        doc = plan.documents[0]
        expected = chunk_document(doc, plan.chunking)
        _require(tuple(c.input.chunk for c in plan.calls) == expected)
        _require(all(c.input.question == plan.calls[0].input.question for c in plan.calls))
        count = len(expected)
        _require(count + 1 <= execution_budget.max_calls, "execution_budget_exceeded")
        _require(
            sum(e.context_tokens for e in estimates) + execution_budget.reduce_reserved_tokens
            <= execution_budget.max_estimated_tokens,
            "execution_budget_exceeded",
        )
        config = document_config(
            plan,
            stage_budget,
            execution_budget,
            pricing,
            estimates,
            reduce_stage_budget=reduce_stage_budget,
        )
        question = session.get(QuestionRow, _identifier(question_id))
        source = session.get(SourceRow, _identifier(source_id))
        _require(
            question is not None
            and source is not None
            and session.get(QuestionSourceRow, (question_id, source_id)) is not None
        )
        _require(doc.identity == SourceIdentity(source.provider, source.scope, source.external_key))
        question_hash = fingerprint(render_document_question(question.title, question.description))
        _require(question_hash == fingerprint(plan.calls[0].input.question))
        result = session.execute(
            insert(executions).values(
                question_id=question_id,
                source_id=source_id,
                status="running",
                provider=provider,
                model=model,
                source_fingerprint=doc.source_fingerprint,
                content_sha256=doc.content_sha256,
                input_chars=len(doc.text),
                question_sha256=question_hash,
                chunk_count=count,
                config_json=_json(config),
                created_at=utc_now(),
            )
        )
        execution_id = result.inserted_primary_key[0]
        for chunk in expected:
            session.execute(
                insert(chunks).values(
                    execution_id=execution_id,
                    ordinal=chunk.ordinal,
                    chunk_id=chunk.chunk_id,
                    start=chunk.start,
                    end=chunk.end,
                    content_sha256=chunk.content_sha256,
                    source_fingerprint=chunk.source_fingerprint,
                )
            )
        for ordinal in range(1, count + 2):
            stage = "map" if ordinal <= count else "document_reduce"
            session.execute(
                insert(stages).values(
                    execution_id=execution_id,
                    ordinal=ordinal,
                    stage=stage,
                    status="planned",
                    prompt_version=PROMPT_VERSIONS[stage],
                    contract_version=CONTRACT_VERSIONS[stage],
                    call_outcome="not_started",
                    created_at=utc_now(),
                )
            )
            if stage == "map":
                session.execute(
                    insert(inputs).values(
                        execution_id=execution_id,
                        parent_ordinal=ordinal,
                        ordinal=1,
                        chunk_ordinal=ordinal,
                    )
                )
            else:
                for child in range(1, count + 1):
                    session.execute(
                        insert(inputs).values(
                            execution_id=execution_id,
                            parent_ordinal=ordinal,
                            ordinal=child,
                            child_ordinal=child,
                        )
                    )
        return DocumentRun(**self._run(session, execution_id))

    def _binding(self, session, run, ordinal, request):
        """Match runtime request to persisted source/plan and exact completed children."""
        config = json.loads(run.config_json)
        _require(
            fingerprint(request.question) == run.question_sha256 and len(request.documents) == 1
        )
        doc = request.documents[0]
        source = session.get(SourceRow, run.source_id)
        _require(
            doc.identity == SourceIdentity(source.provider, source.scope, source.external_key)
            and doc.source_fingerprint == run.source_fingerprint
            and doc.content_sha256 == run.content_sha256
            and len(doc.text) == run.input_chars
        )
        expected_budget = (
            config.get("reduce_stage_budget", config["stage_budget"])
            if request.stage == "document_reduce"
            else config["stage_budget"]
        )
        _require(asdict(request.budget) == expected_budget)
        rows = self._stages(session, run.id)
        _require(
            len(rows) == run.chunk_count + 1
            and [r.ordinal for r in rows] == list(range(1, len(rows) + 1))
        )
        row = rows[ordinal - 1]
        _require(request.stage == row.stage and request.contract_version == row.contract_version)
        manifest = tuple(
            session.execute(
                select(chunks).where(chunks.c.execution_id == run.id).order_by(chunks.c.ordinal)
            ).mappings()
        )
        expected = chunk_document(doc, request.chunking)
        _require(
            asdict(request.chunking) == config["chunking"] and len(manifest) == run.chunk_count
        )
        _require(
            [
                (r.ordinal, r.chunk_id, r.start, r.end, r.content_sha256, r.source_fingerprint)
                for r in manifest
            ]
            == [
                (c.ordinal, c.chunk_id, c.start, c.end, c.content_sha256, c.source_fingerprint)
                for c in expected
            ]
        )
        edges = tuple(
            session.execute(
                select(inputs)
                .where(inputs.c.execution_id == run.id, inputs.c.parent_ordinal == ordinal)
                .order_by(inputs.c.ordinal)
            ).mappings()
        )
        if row.stage == "map":
            _require(
                1 <= ordinal <= run.chunk_count
                and request.chunks == (expected[ordinal - 1],)
                and not request.children
            )
            _require(
                len(edges) == 1
                and edges[0].ordinal == 1
                and edges[0].chunk_ordinal == ordinal
                and edges[0].child_ordinal is None
            )
        else:
            _require(ordinal == run.chunk_count + 1 and len(request.children) == run.chunk_count)
            _require(
                [(e.ordinal, e.child_ordinal, e.chunk_ordinal) for e in edges]
                == [(n, n, None) for n in range(1, run.chunk_count + 1)]
            )
            _require(all(r.status == "completed" and r.stage == "map" for r in rows[:-1]))
            for child, stored, chunk in zip(request.children, rows[:-1], expected, strict=True):
                _require(child.request.stage == "map" and child.request.chunks == (chunk,))
                _require(
                    history_result(child) == stored.result_json
                    and fingerprint(render_stage_input(child.request)) == stored.request_sha256
                )
        return config, rows, row

    def start_stage(
        self, execution_id: int, ordinal: int, request: StageRequest, estimate: RequestEstimate
    ) -> None:
        with self._database._session() as session:
            self._assert_standalone(session, execution_id)
            return self._start_stage(session, execution_id, ordinal, request, estimate)

    def _start_stage(
        self,
        session,
        execution_id: int,
        ordinal: int,
        request: StageRequest,
        estimate: RequestEstimate,
    ) -> None:
        # Rendering replays provenance validation before entering the transaction.
        request_hash = fingerprint(render_stage_input(request))
        run = self._run(session, execution_id, running=True)
        _require(type(ordinal) is int and 1 <= ordinal <= run.chunk_count + 1)
        config, rows, row = self._binding(session, run, ordinal, request)
        _require(row.status == "planned", "immutable_run")
        _require(all(r.status == "completed" for r in rows[: ordinal - 1]))
        _require(all(r.status == "planned" for r in rows[ordinal:]))
        _require(
            type(estimate) is RequestEstimate
            and type(estimate.input_tokens) is int
            and type(estimate.context_tokens) is int
            and estimate.input_tokens > 0
            and estimate.context_tokens
            == estimate.input_tokens
            + request.budget.output_tokens
            + request.budget.safety_margin_tokens
        )
        _name(estimate.estimator_version, 64)
        b = config["execution_budget"]
        code = (
            "reduce_budget_exceeded"
            if request.stage == "document_reduce"
            else "execution_budget_exceeded"
        )
        _require(
            estimate.input_tokens <= request.budget.max_input_tokens
            and estimate.context_tokens <= request.budget.context_tokens,
            code,
        )
        spent = sum(r.estimated_context_tokens or 0 for r in rows if r.status != "planned")
        reserve = b["reduce_reserved_tokens"] if request.stage == "map" else 0
        _require(spent + estimate.context_tokens + reserve <= b["max_estimated_tokens"], code)
        if request.stage == "document_reduce":
            _require(estimate.context_tokens <= b["reduce_reserved_tokens"], code)
        price = _pricing(config)
        cost = price.cost(estimate.input_tokens, request.budget.output_tokens) if price else None
        if b["max_estimated_cost"] is not None:
            from decimal import Decimal

            _require(price is not None, code)
            previous = sum(
                (Decimal(r.estimated_cost) for r in rows if r.estimated_cost is not None),
                Decimal(0),
            )
            reserved_cost = (
                price.cost(
                    config.get("reduce_stage_budget", config["stage_budget"])["max_input_tokens"],
                    config.get("reduce_stage_budget", config["stage_budget"])["output_tokens"],
                )
                if reserve
                else Decimal(0)
            )
            _require(previous + cost + reserved_cost <= Decimal(b["max_estimated_cost"]), code)
        changed = session.execute(
            update(stages)
            .where(
                stages.c.execution_id == execution_id,
                stages.c.ordinal == ordinal,
                stages.c.status == "planned",
            )
            .values(
                status="running",
                started_at=utc_now(),
                request_sha256=request_hash,
                estimated_input_tokens=estimate.input_tokens,
                estimated_context_tokens=estimate.context_tokens,
                estimator_version=estimate.estimator_version,
                estimated_cost=str(cost) if cost is not None else None,
                call_outcome="unknown",
            )
        )
        _require(changed.rowcount == 1, "immutable_run")

    def complete_stage(
        self,
        execution_id: int,
        ordinal: int,
        result: ValidatedStageResult,
        metadata: ProviderResult,
    ) -> DocumentRun:
        with self._database._session() as session:
            self._assert_standalone(session, execution_id)
            return self._complete_stage(session, execution_id, ordinal, result, metadata)

    def _complete_stage(
        self,
        session,
        execution_id: int,
        ordinal: int,
        result: ValidatedStageResult,
        metadata: ProviderResult,
    ) -> DocumentRun:
        verified = validate_stage_output(result.request, result.output_json)
        _require(verified == result, "invalid_output")
        run = self._run(session, execution_id, running=True)
        _require(type(ordinal) is int and 1 <= ordinal <= run.chunk_count + 1)
        config, _, row = self._binding(session, run, ordinal, result.request)
        _require(row.status == "running", "immutable_run")
        _require(row.request_sha256 == fingerprint(render_stage_input(result.request)))
        normalized = history_result(
            result, range(1, run.chunk_count + 1) if row.stage == "document_reduce" else ()
        )
        changed = session.execute(
            update(stages)
            .where(
                stages.c.execution_id == execution_id,
                stages.c.ordinal == ordinal,
                stages.c.status == "running",
            )
            .values(
                status="completed",
                result_json=normalized,
                completed_at=utc_now(),
                call_outcome="response_received",
                **_metadata(metadata, _pricing(config)),
            )
        )
        _require(changed.rowcount == 1, "immutable_run")
        if row.stage == "document_reduce":
            changed = session.execute(
                update(executions)
                .where(executions.c.id == execution_id, executions.c.status == "running")
                .values(status="completed", completed_at=utc_now())
            )
            _require(changed.rowcount == 1, "immutable_run")
        return DocumentRun(**self._run(session, execution_id))

    def fail_run(
        self, execution_id, code, *, interrupted=False, metadata=None, response_received=False
    ):
        with self._database._session() as session:
            self._assert_standalone(session, execution_id)
            return self._fail_run(
                session,
                execution_id,
                code,
                interrupted=interrupted,
                metadata=metadata,
                response_received=response_received,
            )

    def _fail_run(
        self,
        session,
        execution_id,
        code,
        *,
        interrupted=False,
        metadata=None,
        response_received=False,
    ):
        _require(code in PIPELINE_ERRORS)
        terminal = "interrupted" if interrupted else "failed"
        run = self._run(session, execution_id, running=True)
        values = _metadata(metadata, _pricing(json.loads(run.config_json)))
        session.execute(
            update(stages)
            .where(stages.c.execution_id == execution_id, stages.c.status == "running")
            .values(
                status=terminal,
                error_code=code,
                completed_at=utc_now(),
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
            .where(stages.c.execution_id == execution_id, stages.c.status == "planned")
            .values(status="not_started", error_code=code, completed_at=utc_now())
        )
        changed = session.execute(
            update(executions)
            .where(executions.c.id == execution_id, executions.c.status == "running")
            .values(status=terminal, error_code=code, completed_at=utc_now())
        )
        _require(changed.rowcount == 1, "immutable_run")
        return DocumentRun(**self._run(session, execution_id))

    @staticmethod
    def _assert_standalone(session, execution_id):
        from research_workbench.analysis.cross_document_pipeline import CrossConflict

        from .cross_document_models import members

        if (
            session.execute(
                select(members.c.run_id).where(members.c.document_execution_id == execution_id)
            ).first()
            is not None
        ):
            raise CrossConflict()
