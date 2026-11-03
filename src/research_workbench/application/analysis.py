"""Synchronous analysis orchestration. No SDK imports and no transaction across I/O."""

from research_workbench.analysis.contracts import PROMPT_VERSION, AnalysisProvider
from research_workbench.analysis.corpus import ResearchCorpusBuilder, manifest
from research_workbench.analysis.errors import (
    FAILURE_MESSAGES,
    AnalysisError,
    AnalysisPersistenceError,
    AnalysisProviderError,
)
from research_workbench.analysis.schema import validate_and_map
from research_workbench.domain.analysis import AnalysisRun
from research_workbench.persistence import (
    Database,
    EntityNotFoundError,
    QuestionRepository,
    QuestionSourceRepository,
)
from research_workbench.persistence.analysis_repository import AnalysisRepository
from research_workbench.persistence.errors import PersistenceError


class AnalysisService:
    def __init__(
        self, database: Database, corpus: ResearchCorpusBuilder, provider: AnalysisProvider
    ) -> None:
        self._questions = QuestionRepository(database)
        self._links = QuestionSourceRepository(database)
        self._runs = AnalysisRepository(database)
        self._corpus = corpus
        self._provider = provider

    def run_analysis(self, question_id: int, mode: str = "abstracts") -> AnalysisRun:
        question = self._questions.get(question_id)
        if question is None:
            raise EntityNotFoundError("Question", question_id)
        # Fetch max_sources+1: detect excess without silently ignoring further linked sources.
        links = self._links.list_for_question(
            question_id, limit=self._corpus.budget.max_sources + 1
        )
        request = self._corpus.build(question, links, mode)
        run = self._runs.create_run(
            question_id,
            provider=self._provider.provider,
            model=self._provider.model,
            sources=manifest(request),
            prompt_version=getattr(self._provider, "prompt_version", PROMPT_VERSION),
        )
        try:
            result = self._provider.generate(request)
            normalized = validate_and_map(
                result.output_json, {s.label: s.source_id for s in request.sources}
            )
            return self._runs.complete_run(
                run.id,
                normalized,
                response_id=result.response_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        except PersistenceError:
            try:
                self._runs.fail_run(run.id, "persistence_failed")
            except PersistenceError:
                pass  # Storage remains unavailable; do not misreport a provider failure.
            raise AnalysisPersistenceError() from None
        except AnalysisError as exc:
            self._runs.fail_run(
                run.id,
                exc.code if exc.code in FAILURE_MESSAGES else "analysis_failed",
            )
            raise
        except Exception:
            # Preserve no raw exception/cause, including unexpected SDK/plugin error payloads.
            self._runs.fail_run(run.id, "provider_failed")
            raise AnalysisProviderError() from None
