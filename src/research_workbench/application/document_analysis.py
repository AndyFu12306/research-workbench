"""One explicit linked document, all Maps, then one Reduce. No retry or resume."""

import json
from typing import Protocol

from research_workbench.analysis.chunking import (
    ChunkingConfig,
    SourceDocument,
    SourceIdentity,
)
from research_workbench.analysis.document_pipeline import (
    PIPELINE_ERRORS,
    BudgetedStageProvider,
    DocumentAnalysisError,
    DocumentExecutionOutcome,
    DocumentRun,
    ExecutionBudget,
    PreparedDocument,
    Pricing,
    RequestEstimate,
    render_document_question,
)
from research_workbench.analysis.errors import AnalysisError
from research_workbench.analysis.planning import MapBudgetPlanner, MapTemplate, PlanningBudget
from research_workbench.analysis.stage_contracts import (
    CONTRACT_VERSIONS,
    StageBudget,
    StageBudgetError,
    StageError,
    StageRequest,
    stage_instructions,
    stage_schema,
)
from research_workbench.analysis.stage_validation import validate_budget, validate_stage_output
from research_workbench.integrations.zotero.models import ZoteroFulltext
from research_workbench.persistence import Database
from research_workbench.persistence.document_repository import DocumentAnalysisRepository
from research_workbench.persistence.errors import PersistenceError


class FulltextReader(Protocol):
    """Production implementation is ZoteroClient, including its completeness checks."""

    def health_check(self): ...

    def get_fulltext(self, item_key: str) -> ZoteroFulltext: ...


class DocumentAnalysisService:
    def __init__(
        self,
        database: Database,
        reader: FulltextReader,
        provider: BudgetedStageProvider,
        *,
        chunking: ChunkingConfig | None = None,
        planning_budget: PlanningBudget | None = None,
        stage_budget: StageBudget | None = None,
        reduce_stage_budget: StageBudget | None = None,
        execution_budget: ExecutionBudget | None = None,
        pricing: Pricing | None = None,
    ):
        self._reader = reader
        self._provider = provider
        self._stage_budget = stage_budget if stage_budget is not None else StageBudget()
        self._reduce_stage_budget = (
            reduce_stage_budget if reduce_stage_budget is not None else self._stage_budget
        )
        self._budget = (
            execution_budget
            if execution_budget is not None
            else (
                ExecutionBudget(
                    reduce_reserved_tokens=self._reduce_stage_budget.max_input_tokens
                    + self._reduce_stage_budget.output_tokens
                    + self._reduce_stage_budget.safety_margin_tokens
                )
                if reduce_stage_budget is not None
                else ExecutionBudget()
            )
        )
        if type(self._budget) is not ExecutionBudget:
            raise DocumentAnalysisError("execution_budget_exceeded")
        self._pricing = pricing
        self._runs = DocumentAnalysisRepository(database)
        validate_budget(self._stage_budget)
        validate_budget(self._reduce_stage_budget)
        if planning_budget is None:
            planning_budget = PlanningBudget(
                context_tokens=self._stage_budget.context_tokens,
                max_input_tokens=self._stage_budget.max_input_tokens,
                reserved_output_tokens=self._stage_budget.output_tokens,
                safety_margin_tokens=self._stage_budget.safety_margin_tokens,
                protocol_overhead_tokens=self._stage_budget.protocol_overhead_tokens,
            )
        self._planner = MapBudgetPlanner(chunking=chunking, budget=planning_budget)
        if pricing is not None and type(pricing) is not Pricing:
            raise DocumentAnalysisError("execution_budget_exceeded")
        if self._budget.max_estimated_cost is not None and pricing is None:
            raise DocumentAnalysisError("execution_budget_exceeded")

    def run_analysis(self, question_id: int, source_id: int) -> DocumentRun:
        question, source = self._runs.get_selection(question_id, source_id)
        question_input = render_document_question(question.title, question.description)
        if (source.provider, source.scope) != ("zotero", "personal"):
            raise DocumentAnalysisError("source_unavailable")
        if not all(
            (stage, CONTRACT_VERSIONS[stage]) in self._provider.capabilities
            for stage in ("map", "document_reduce")
        ):
            raise DocumentAnalysisError("invalid_plan")
        try:
            if self._reader.health_check().status != "healthy":
                raise DocumentAnalysisError("source_unavailable")
            fulltext = self._reader.get_fulltext(source.external_key)
            if type(fulltext) is not ZoteroFulltext or fulltext.item_key != source.external_key:
                raise DocumentAnalysisError("source_unavailable")
            document = SourceDocument(
                SourceIdentity(source.provider, source.scope, source.external_key), fulltext.text
            )
        except Exception:
            raise DocumentAnalysisError("source_unavailable") from None
        prepared = self.prepare_document(question_input, document)
        run = self.register_document(question_id, source_id, prepared)
        return self.execute_registered(run, prepared).run

    def prepare_document(self, question_input, document) -> PreparedDocument:
        """Build and preflight a frozen plan without reading or writing external state."""
        plan = self._planner.build(
            question_input,
            (document,),
            MapTemplate(
                stage_instructions("map"),
                json.dumps(stage_schema("map", CONTRACT_VERSIONS["map"]), separators=(",", ":")),
            ),
        )
        requests = tuple(
            StageRequest(
                "map",
                CONTRACT_VERSIONS["map"],
                question_input,
                (document,),
                budget=self._stage_budget,
                chunks=(call.input.chunk,),
                chunking=plan.chunking,
            )
            for call in plan.calls
        )
        estimates = tuple(self._estimate(request) for request in requests)
        if len(requests) + 1 > self._budget.max_calls or (
            sum(e.context_tokens for e in estimates) + self._budget.reduce_reserved_tokens
            > self._budget.max_estimated_tokens
        ):
            raise DocumentAnalysisError("execution_budget_exceeded")
        if self._budget.max_estimated_cost is not None:
            total_cost = sum(
                self._pricing.cost(e.input_tokens, self._stage_budget.output_tokens)
                for e in estimates
            )
            total_cost += self._pricing.cost(
                self._reduce_stage_budget.max_input_tokens, self._reduce_stage_budget.output_tokens
            )
            if total_cost > self._budget.max_estimated_cost:
                raise DocumentAnalysisError("execution_budget_exceeded")
        return PreparedDocument(plan, requests, estimates)

    def register_document(self, question_id, source_id, prepared) -> DocumentRun:
        try:
            return self._runs.create_run(
                question_id,
                source_id,
                prepared.plan,
                provider=self._provider.provider,
                model=self._provider.model,
                stage_budget=self._stage_budget,
                reduce_stage_budget=self._reduce_stage_budget,
                execution_budget=self._budget,
                pricing=self._pricing,
                estimates=prepared.estimates,
            )
        except PersistenceError:
            raise DocumentAnalysisError("persistence_failed") from None

    def execute_registered(
        self,
        run,
        prepared,
        *,
        _repository=None,
        _before_stage=None,
        _before_call=None,
        _response=None,
        _managed=False,
    ) -> DocumentExecutionOutcome:
        """Execute precisely the registered plan; no reads, retries or plan rebuilding."""
        requests, plan = prepared.requests, prepared.plan
        question_input, document = requests[0].question, plan.documents[0]
        repository = self._runs if _repository is None else _repository
        metadata = None
        children = []
        stage = "map"
        try:
            for ordinal, request in enumerate(requests, 1):
                metadata = None
                if _before_stage is not None:
                    _before_stage()
                estimate = self._estimate(request)
                if _before_stage is not None:
                    _before_stage()
                repository.start_stage(run.id, ordinal, request, estimate)
                if _before_stage is not None:
                    _before_stage()
                if _before_call is not None:
                    _before_call()
                metadata = self._provider.generate_stage(request)
                if _response is not None:
                    _response(metadata)
                validated = validate_stage_output(request, metadata.output_json)
                repository.complete_stage(run.id, ordinal, validated, metadata)
                children.append(validated)
            stage = "document_reduce"
            metadata = None
            if _before_stage is not None:
                _before_stage()
            request = StageRequest(
                stage,
                CONTRACT_VERSIONS[stage],
                question_input,
                (document,),
                budget=self._reduce_stage_budget,
                chunking=plan.chunking,
                children=tuple(children),
            )
            # start_stage also checks the persisted manifest, every Map and ordered edge.
            estimate = self._estimate(request)
            if _before_stage is not None:
                _before_stage()
            repository.start_stage(run.id, len(requests) + 1, request, estimate)
            if _before_stage is not None:
                _before_stage()
            if _before_call is not None:
                _before_call()
            metadata = self._provider.generate_stage(request)
            if _response is not None:
                _response(metadata)
            validated = validate_stage_output(request, metadata.output_json)
            completed = repository.complete_stage(run.id, len(requests) + 1, validated, metadata)
            return DocumentExecutionOutcome(completed, len(requests) + 1, validated)
        except KeyboardInterrupt:
            if _managed:
                raise
            self._fail(run.id, "interrupted", metadata=metadata, interrupted=True)
            raise KeyboardInterrupt() from None
        except Exception as exc:
            if _managed:
                raise
            if isinstance(exc, PersistenceError):
                code = "persistence_failed"
            elif isinstance(exc, StageBudgetError):
                code = (
                    "reduce_budget_exceeded"
                    if stage == "document_reduce"
                    else "execution_budget_exceeded"
                )
            elif isinstance(exc, StageError):
                code = "invalid_output"
            elif isinstance(exc, (DocumentAnalysisError, AnalysisError)):
                code = exc.code if exc.code in PIPELINE_ERRORS else "analysis_failed"
            else:
                code = "provider_failed"
            self._fail(run.id, code, metadata=metadata)
            raise DocumentAnalysisError(code) from None

    def _estimate(self, request: StageRequest) -> RequestEstimate:
        try:
            estimate = self._provider.estimate_stage(request)
        except StageError as exc:
            raise exc from None
        except Exception:
            raise DocumentAnalysisError("execution_budget_exceeded") from None
        if (
            type(estimate) is not RequestEstimate
            or type(estimate.input_tokens) is not int
            or type(estimate.context_tokens) is not int
            or estimate.input_tokens < 1
            or estimate.context_tokens
            != estimate.input_tokens
            + request.budget.output_tokens
            + request.budget.safety_margin_tokens
        ):
            raise DocumentAnalysisError("execution_budget_exceeded")
        if (
            estimate.input_tokens > request.budget.max_input_tokens
            or estimate.context_tokens > request.budget.context_tokens
        ):
            raise StageBudgetError()
        return estimate

    def _fail(self, execution_id, code, *, metadata=None, interrupted=False):
        try:
            self._runs.fail_run(execution_id, code, metadata=metadata, interrupted=interrupted)
        except DocumentAnalysisError:
            # Malformed optional provider metadata must not prevent safe failure history.
            try:
                self._runs.fail_run(
                    execution_id,
                    code,
                    interrupted=interrupted,
                    response_received=metadata is not None,
                )
            except (PersistenceError, DocumentAnalysisError):
                pass
        except PersistenceError:
            pass  # Persistent storage failure/forced exit may leave running, never success.
