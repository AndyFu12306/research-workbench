"""Explicit ordered selection, frozen plans, sequential execution and one Cross."""

from research_workbench.analysis.chunking import SourceDocument, SourceIdentity
from research_workbench.analysis.cross_document_pipeline import (
    CrossBudget,
    CrossConflict,
    CrossDocumentError,
    FrozenSelection,
    PreparedCross,
    allocate_budget,
    require,
    validate_selection,
    validate_topology,
)
from research_workbench.analysis.document_pipeline import (
    DocumentRun,
    ExecutionBudget,
    render_document_question,
)
from research_workbench.analysis.errors import AnalysisProviderFailure
from research_workbench.analysis.stage_contracts import (
    CONTRACT_VERSIONS,
    StageBudget,
    StageBudgetError,
    StageRequest,
)
from research_workbench.analysis.stage_validation import validate_stage_output
from research_workbench.integrations.zotero.models import ZoteroFulltext
from research_workbench.persistence.cross_document_repository import CrossDocumentRepository
from research_workbench.persistence.document_repository import DocumentAnalysisRepository
from research_workbench.persistence.errors import PersistenceError
from research_workbench.persistence.models import utc_now

from .document_analysis import DocumentAnalysisService


class _DocumentTransactions:
    """Private adapter: all child mutations pass the same parent ownership gate."""

    def __init__(self, repository, handle, prepared, member_ordinal):
        self.repository, self.handle, self.prepared = repository, handle, prepared
        self.member_ordinal = member_ordinal

    def start_stage(self, execution_id, ordinal, request, estimate):
        require(execution_id == self.handle.bindings[self.member_ordinal - 1].document_execution_id)
        self.repository.start_document_stage(
            self.handle, self.prepared, self.member_ordinal, ordinal, request, estimate
        )

    def complete_stage(self, execution_id, ordinal, result, metadata):
        require(execution_id == self.handle.bindings[self.member_ordinal - 1].document_execution_id)
        return self.repository.complete_document_stage(
            self.handle, self.prepared, self.member_ordinal, ordinal, result, metadata
        )


class CrossDocumentAnalysisService:
    def __init__(
        self,
        database,
        reader,
        provider,
        *,
        chunking=None,
        planning_budget=None,
        stage_budget=None,
        cross_budget=None,
        pricing=None,
    ):
        self._reader, self._provider = reader, provider
        self._budget = cross_budget if cross_budget is not None else CrossBudget()
        require(type(self._budget) is CrossBudget)
        require(self._budget.max_estimated_cost is None or pricing is not None, "budget_exceeded")
        self._pricing = pricing
        effective_stage_budget = stage_budget if stage_budget is not None else StageBudget()
        require(type(effective_stage_budget) is StageBudget)
        require(
            self._budget.max_estimated_tokens
            >= effective_stage_budget.max_input_tokens
            + effective_stage_budget.output_tokens
            + effective_stage_budget.safety_margin_tokens,
            "budget_exceeded",
        )
        self._documents = DocumentAnalysisService(
            database,
            reader,
            provider,
            chunking=chunking,
            planning_budget=planning_budget,
            stage_budget=effective_stage_budget,
            execution_budget=ExecutionBudget(
                101,
                self._budget.max_estimated_tokens,
                effective_stage_budget.max_input_tokens
                + effective_stage_budget.output_tokens
                + effective_stage_budget.safety_margin_tokens,
            ),
            pricing=pricing,
        )
        self._selection = DocumentAnalysisRepository(database)
        self._runs = CrossDocumentRepository(database)

    def _prepare(self, question_id, source_ids):
        validate_selection(source_ids)
        require(
            all(
                (stage, version) in self._provider.capabilities
                for stage, version in CONTRACT_VERSIONS.items()
            )
        )
        provider, model = self._provider.provider, self._provider.model
        records = tuple(self._selection.get_selection(question_id, sid) for sid in source_ids)
        question = render_document_question(records[0][0].title, records[0][0].description)
        require(
            all(render_document_question(q.title, q.description) == question for q, _ in records)
        )
        require(self._reader.health_check().status == "healthy", "source_unavailable")
        documents, read_at = [], []
        for _, source in records:
            require((source.provider, source.scope) == ("zotero", "personal"), "source_unavailable")
            fulltext = self._reader.get_fulltext(source.external_key)
            require(
                type(fulltext) is ZoteroFulltext and fulltext.item_key == source.external_key,
                "source_unavailable",
            )
            documents.append(
                SourceDocument(
                    SourceIdentity(source.provider, source.scope, source.external_key),
                    fulltext.text,
                )
            )
            read_at.append(utc_now())
        selection = FrozenSelection(
            question_id, source_ids, question, tuple(documents), tuple(read_at), provider, model
        )
        plans = tuple(self._documents.prepare_document(question, doc) for doc in documents)
        require((self._provider.provider, self._provider.model) == (provider, model))
        require(
            all(
                (stage, version) in self._provider.capabilities
                for stage, version in CONTRACT_VERSIONS.items()
            )
        )
        allocation = allocate_budget(
            tuple(p.estimates for p in plans),
            self._documents._stage_budget,
            self._budget,
            self._pricing,
        )
        return PreparedCross(
            selection, plans, allocation, self._documents._stage_budget, self._budget, self._pricing
        )

    def run_analysis(self, question_id, source_ids):
        try:
            prepared = self._prepare(question_id, source_ids)
            handle = self._runs.register(prepared)
        except CrossConflict:
            raise CrossConflict() from None
        except CrossDocumentError as exc:
            raise CrossDocumentError(exc.code) from None
        except PersistenceError:
            raise CrossDocumentError("persistence_failed") from None
        except Exception:
            raise CrossDocumentError("invalid_plan") from None

        metadata = None
        active_document_id = None
        call_attempted = False
        response_received = False

        def before_stage():
            nonlocal metadata, call_attempted, response_received
            metadata = None
            call_attempted = False
            response_received = False
            require(
                (self._provider.provider, self._provider.model)
                == (prepared.selection.provider, prepared.selection.model)
            )
            require(
                all(
                    (stage, version) in self._provider.capabilities
                    for stage, version in CONTRACT_VERSIONS.items()
                )
            )

        def response(value):
            nonlocal metadata, response_received
            metadata = value
            response_received = True

        def before_call():
            nonlocal call_attempted
            call_attempted = True

        try:
            outcomes = []
            for b, plan in zip(handle.bindings, prepared.documents, strict=True):
                active_document_id = b.document_execution_id
                run = self._selection.get(b.document_execution_id)
                require(type(run) is DocumentRun)
                outcome = self._documents.execute_registered(
                    run,
                    plan,
                    _repository=_DocumentTransactions(self._runs, handle, prepared, b.ordinal),
                    _before_stage=before_stage,
                    _before_call=before_call,
                    _response=response,
                    _managed=True,
                )
                outcomes.append(outcome)
            before_stage()
            active_document_id = None
            request = StageRequest(
                "cross_document_reduce",
                CONTRACT_VERSIONS["cross_document_reduce"],
                prepared.selection.question,
                prepared.selection.documents,
                budget=prepared.stage_budget,
                chunking=prepared.documents[0].plan.chunking,
                children=tuple(o.result for o in outcomes),
            )
            validate_topology(request, prepared, tuple(outcomes))
            estimate = self._documents._estimate(request)
            before_stage()
            self._runs.start_cross(handle, prepared, request, tuple(outcomes), estimate)
            before_stage()
            before_call()
            metadata = self._provider.generate_stage(request)
            response(metadata)
            result = validate_stage_output(request, metadata.output_json)
            return self._runs.complete_cross(handle, prepared, result, tuple(outcomes), metadata)
        except CrossConflict:
            # A competing claimant/finalizer never cleans up the winner's execution.
            raise CrossConflict() from None
        except KeyboardInterrupt:
            self._abort(
                handle,
                "interrupted",
                metadata,
                interrupted=True,
                active_document_id=active_document_id,
                response_received=response_received,
            )
            raise KeyboardInterrupt() from None
        except Exception as exc:
            # A commit acknowledgement can fail after T4 committed. Read only, never re-call.
            try:
                current = self._runs.get(handle.run_id)
                if current is not None and current.status == "completed":
                    self._runs.get_history(handle.run_id)
                    return current
            except Exception:
                pass
            code = (
                exc.code
                if isinstance(exc, CrossDocumentError)
                else (
                    "persistence_failed" if isinstance(exc, PersistenceError) else "provider_failed"
                )
            )
            # SDK incompatibility also wraps exceptions from responses.create itself.
            # Its code alone cannot prove receipt; only an explicit return overrides this.
            unknown = not response_received and (
                getattr(exc, "code", None)
                in ("provider_timeout", "provider_network", "provider_sdk_incompatible")
                or isinstance(exc, (TimeoutError, ConnectionError))
                or (call_attempted and not isinstance(exc, AnalysisProviderFailure))
            )
            if isinstance(exc, StageBudgetError) or getattr(exc, "code", None) in (
                "execution_budget_exceeded",
                "reduce_budget_exceeded",
            ):
                code = "budget_exceeded"
            if unknown:
                code = "interrupted"
            elif response_received and code == "provider_failed":
                code = "invalid_output"
            self._abort(
                handle,
                code,
                metadata,
                interrupted=unknown,
                active_document_id=active_document_id,
                response_received=response_received
                or getattr(exc, "code", None)
                in (
                    "provider_refusal",
                    "provider_incomplete",
                    "provider_invalid_json",
                    "provider_empty_json",
                ),
            )
            raise CrossDocumentError(code) from None

    def _abort(
        self,
        handle,
        code,
        metadata,
        *,
        interrupted,
        active_document_id=None,
        response_received=False,
    ):
        try:
            self._runs.abort(
                handle,
                code,
                interrupted=interrupted,
                metadata=metadata,
                active_document_id=active_document_id,
                response_received=response_received,
            )
        except CrossConflict:
            pass
        except Exception:
            try:
                self._runs.abort(
                    handle,
                    code,
                    interrupted=interrupted,
                    response_received=metadata is not None or response_received,
                    active_document_id=active_document_id,
                )
            except Exception:
                pass  # Ongoing DB failure may leave running; no claim of persisted failure.
