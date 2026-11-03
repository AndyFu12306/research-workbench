"""Read existing sources into bounded ephemeral inputs; never silently omit one."""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from research_workbench.domain import Question, QuestionSource
from research_workbench.domain.analysis import AnalysisSourceInput
from research_workbench.integrations.zotero import (
    ZoteroCliNotFoundError,
    ZoteroCommandError,
    ZoteroError,
    ZoteroFulltext,
    ZoteroItem,
    ZoteroItemNotFoundError,
    ZoteroParseError,
    ZoteroTimeoutError,
    ZoteroUnavailableError,
)

from .contracts import INSTRUCTIONS, AnalysisRequest, CorpusSource
from .errors import (
    AnalysisCorpusIncompleteError,
    AnalysisInputTooLargeError,
    AnalysisNoSourcesError,
    AnalysisRetrievalUnavailableError,
)
from .schema import OUTPUT_SCHEMA


class CorpusReader(Protocol):
    def health_check(self): ...

    def get_item(self, item_key: str) -> ZoteroItem: ...
    def get_fulltext(self, item_key: str) -> ZoteroFulltext: ...


@dataclass(frozen=True, slots=True)
class InputBudget:
    max_sources: int = 5
    max_chars_per_source: int = 24_000
    max_total_chars: int = 60_000

    def __post_init__(self):
        if any(
            type(v) is not int or v < 1
            for v in (self.max_sources, self.max_chars_per_source, self.max_total_chars)
        ):
            raise ValueError("Input budgets must be positive integers.")
        if self.max_sources > 5:
            raise ValueError("analysis-schema-v1 supports at most five sources.")


def manifest(request: AnalysisRequest) -> tuple[AnalysisSourceInput, ...]:
    return tuple(
        AnalysisSourceInput(
            0,
            s.source_id,
            ordinal,
            s.input_kind,
            hashlib.sha256(s.content.encode("utf-8")).hexdigest(),
            len(s.content),
        )
        for ordinal, s in enumerate(request.sources, 1)
    )


class ResearchCorpusBuilder:
    def __init__(self, zotero: CorpusReader, budget: InputBudget | None = None) -> None:
        self._zotero = zotero
        self.budget = budget or InputBudget()

    def build(
        self, question: Question, links: tuple[QuestionSource, ...], mode: str
    ) -> AnalysisRequest:
        if mode not in {"abstracts", "fulltext_preferred"}:
            raise ValueError("mode must be abstracts or fulltext_preferred.")
        if not links:
            raise AnalysisNoSourcesError()
        if len(links) > self.budget.max_sources:
            raise AnalysisInputTooLargeError()
        if self._zotero.health_check().status != "healthy":
            raise AnalysisRetrievalUnavailableError()
        sources = []
        for link in links:
            ref = link.source
            if (ref.provider, ref.scope) != ("zotero", "personal"):
                raise AnalysisCorpusIncompleteError(source_id=ref.id)
            try:
                item = self._zotero.get_item(ref.external_key)
                if item.item_key != ref.external_key or item.item_type in {
                    "attachment",
                    "note",
                    "annotation",
                }:
                    raise AnalysisCorpusIncompleteError(source_id=ref.id)
            except ZoteroItemNotFoundError:
                raise AnalysisCorpusIncompleteError(source_id=ref.id) from None
            except ZoteroError:
                raise AnalysisRetrievalUnavailableError(source_id=ref.id) from None
            text, kind = item.abstract or "", "abstract"
            if mode == "fulltext_preferred":
                kind = "abstract_fallback"
                try:
                    fulltext = self._zotero.get_fulltext(ref.external_key)
                    if fulltext.item_key != ref.external_key:
                        raise AnalysisRetrievalUnavailableError(source_id=ref.id)
                    if fulltext.text.strip():
                        text, kind = fulltext.text, "fulltext"
                except (ZoteroUnavailableError, ZoteroCliNotFoundError, ZoteroTimeoutError):
                    raise AnalysisRetrievalUnavailableError(source_id=ref.id) from None
                except ZoteroItemNotFoundError:
                    raise AnalysisCorpusIncompleteError(source_id=ref.id) from None
                except (ZoteroCommandError, ZoteroParseError):
                    if self._zotero.health_check().status != "healthy":
                        raise AnalysisRetrievalUnavailableError(source_id=ref.id) from None
                    # Fulltext unavailable/incomplete: use a current abstract explicitly.
                    # Metadata retrieval already succeeded; never use stale metadata.
                    kind = "abstract_fallback"
                except ZoteroError:
                    raise AnalysisRetrievalUnavailableError(source_id=ref.id) from None
            if not text.strip():
                raise AnalysisCorpusIncompleteError(source_id=ref.id)
            content = json.dumps(
                {"title": item.title, "authors": item.authors, "input_kind": kind, "text": text},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(content) > self.budget.max_chars_per_source:
                raise AnalysisInputTooLargeError(source_id=ref.id)
            sources.append(CorpusSource(ref.id, f"S{len(sources) + 1}", kind, content))
        request = AnalysisRequest(
            json.dumps(
                {"title": question.title, "description": question.description},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            tuple(sources),
        )
        size = len(request.render_input()) + len(INSTRUCTIONS) + len(json.dumps(OUTPUT_SCHEMA))
        if size > self.budget.max_total_chars:
            raise AnalysisInputTooLargeError()
        return request
