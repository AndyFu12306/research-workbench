"""Short transactions, conditional finalization, no arbitrary persisted error text."""

import re
from dataclasses import fields

from sqlalchemy import select, update

from research_workbench.analysis.contracts import PROMPT_VERSION, SCHEMA_VERSION
from research_workbench.analysis.errors import (
    FAILURE_MESSAGES,
    AnalysisOutputValidationError,
    AnalysisStateError,
)
from research_workbench.analysis.schema import validate_mapped_result
from research_workbench.domain.analysis import AnalysisRun, AnalysisSourceInput

from .analysis_models import AnalysisRunRow, AnalysisSourceInputRow
from .database import Database
from .errors import EntityNotFoundError
from .models import QuestionRow, utc_now
from .repositories import _identifier, _pagination
from .source_models import SourceRow


def _dto(row, cls):
    return cls(**{f.name: getattr(row, f.name) for f in fields(cls)})


def _name(value: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", value)
        or len(value) > maximum
    ):
        raise ValueError("Invalid analysis identifier.")
    return value


def _usage(value):
    if value is not None and (type(value) is not int or value < 0):
        raise AnalysisOutputValidationError()
    return value


class AnalysisRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_run(
        self,
        question_id: int,
        *,
        provider: str,
        model: str,
        sources: tuple[AnalysisSourceInput, ...],
    ) -> AnalysisRun:
        provider, model = _name(provider, 64), _name(model, 128)
        if not sources or [s.ordinal for s in sources] != list(range(1, len(sources) + 1)):
            raise ValueError("A non-empty, contiguous source manifest is required.")
        if len({s.source_id for s in sources}) != len(sources):
            raise ValueError("Duplicate manifest source.")
        with self._database._session() as session:
            if session.get(QuestionRow, _identifier(question_id)) is None:
                raise EntityNotFoundError("Question", question_id)
            row = AnalysisRunRow(
                question_id=question_id,
                status="running",
                provider=provider,
                model=model,
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
            )
            session.add(row)
            session.flush()
            for source in sources:
                if session.get(SourceRow, _identifier(source.source_id)) is None:
                    raise EntityNotFoundError("SourceRef", source.source_id)
                if (
                    source.input_kind not in {"abstract", "fulltext", "abstract_fallback"}
                    or not re.fullmatch(r"[a-f0-9]{64}", source.content_sha256)
                    or type(source.input_chars) is not int
                    or source.input_chars < 1
                ):
                    raise ValueError("Invalid source manifest.")
                session.add(
                    AnalysisSourceInputRow(
                        analysis_id=row.id,
                        source_id=source.source_id,
                        ordinal=source.ordinal,
                        input_kind=source.input_kind,
                        content_sha256=source.content_sha256,
                        input_chars=source.input_chars,
                    )
                )
            session.flush()
            return _dto(row, AnalysisRun)

    def get(self, analysis_id: int) -> AnalysisRun | None:
        with self._database._session() as session:
            row = session.get(AnalysisRunRow, _identifier(analysis_id))
            return _dto(row, AnalysisRun) if row else None

    def list_by_question(
        self, question_id: int, *, limit: int = 100, offset: int = 0
    ) -> tuple[AnalysisRun, ...]:
        _pagination(limit, offset)
        with self._database._session() as session:
            rows = session.scalars(
                select(AnalysisRunRow)
                .where(AnalysisRunRow.question_id == _identifier(question_id))
                .order_by(AnalysisRunRow.id)
                .limit(limit)
                .offset(offset)
            )
            return tuple(_dto(row, AnalysisRun) for row in rows)

    def get_sources(self, analysis_id: int) -> tuple[AnalysisSourceInput, ...]:
        with self._database._session() as session:
            rows = session.scalars(
                select(AnalysisSourceInputRow)
                .where(AnalysisSourceInputRow.analysis_id == _identifier(analysis_id))
                .order_by(AnalysisSourceInputRow.ordinal)
            )
            return tuple(_dto(row, AnalysisSourceInput) for row in rows)

    def complete_run(
        self,
        analysis_id: int,
        result_json: str,
        *,
        response_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> AnalysisRun:
        manifest = self.get_sources(analysis_id)
        normalized = validate_mapped_result(
            result_json, {f"S{s.ordinal}": s.source_id for s in manifest}
        )
        if response_id is not None and (
            not isinstance(response_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", response_id)
        ):
            raise AnalysisOutputValidationError()
        return self._finish(
            analysis_id,
            status="completed",
            result_json=normalized,
            provider_response_id=response_id,
            input_tokens=_usage(input_tokens),
            output_tokens=_usage(output_tokens),
            error_code=None,
            error_message=None,
        )

    def fail_run(self, analysis_id: int, error_code: str) -> AnalysisRun:
        if error_code not in FAILURE_MESSAGES:
            raise ValueError("Unknown safe failure code.")
        return self._finish(
            analysis_id,
            status="failed",
            result_json=None,
            error_code=error_code,
            error_message=FAILURE_MESSAGES[error_code],
        )

    def _finish(self, analysis_id: int, **values) -> AnalysisRun:
        with self._database._session() as session:
            row = session.get(AnalysisRunRow, _identifier(analysis_id))
            if row is None:
                raise EntityNotFoundError("AnalysisRun", analysis_id)
            changed = session.execute(
                update(AnalysisRunRow)
                .where(AnalysisRunRow.id == analysis_id, AnalysisRunRow.status == "running")
                .values(**values, completed_at=utc_now())
            )
            if changed.rowcount != 1:
                raise AnalysisStateError()
            session.refresh(row)
            return _dto(row, AnalysisRun)
