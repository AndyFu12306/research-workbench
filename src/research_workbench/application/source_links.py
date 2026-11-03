"""Reference linking and ephemeral metadata hydration. No PDF or AI operations."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from research_workbench.domain import QuestionSource, SourceRef
from research_workbench.integrations.zotero import (
    ZoteroCliNotFoundError,
    ZoteroCommandError,
    ZoteroError,
    ZoteroItem,
    ZoteroItemNotFoundError,
    ZoteroParseError,
    ZoteroUnavailableError,
)
from research_workbench.persistence import (
    Database,
    EntityNotFoundError,
    QuestionRepository,
    QuestionSourceRepository,
)


class ZoteroReader(Protocol):
    def get_item(self, item_key: str) -> ZoteroItem: ...


class HydrationStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LinkedSource:
    association: QuestionSource
    status: HydrationStatus
    metadata: ZoteroItem | None = None
    error_code: str | None = None

    @property
    def source(self) -> SourceRef:
        return self.association.source


def _validate_item(item: ZoteroItem, item_key: str) -> ZoteroItem:
    if not isinstance(item, ZoteroItem) or item.item_key != item_key:
        raise ZoteroParseError("Zotero metadata identity did not match the requested source.")
    if item.item_type in {"attachment", "note", "annotation"}:
        raise ValueError(
            "Link the bibliographic parent item, not an attachment, note or annotation."
        )
    return item


class QuestionSourceService:
    def __init__(self, database: Database, zotero: ZoteroReader) -> None:
        self._questions = QuestionRepository(database)
        self._links = QuestionSourceRepository(database)
        self._zotero = zotero

    def attach_zotero_source(self, question_id: int, item_key: str) -> LinkedSource:
        if self._questions.get(question_id) is None:
            raise EntityNotFoundError("Question", question_id)
        # All external validation happens BEFORE any write transaction; no DB lock during CLI I/O.
        item = _validate_item(self._zotero.get_item(item_key), item_key)
        # Re-check Question in the same transaction as SourceRef + association creation.
        association = self._links.attach_reference(question_id, item_key)
        return LinkedSource(association, HydrationStatus.RESOLVED, item)

    def list_question_sources(
        self, question_id: int, *, hydrate: bool = True, limit: int = 100, offset: int = 0
    ) -> tuple[LinkedSource, ...] | tuple[QuestionSource, ...]:
        references = self._links.list_for_question(question_id, limit=limit, offset=offset)
        if not hydrate:
            return references
        return tuple(self._hydrate(link) for link in references)

    def detach_source(self, question_id: int, source_id: int) -> bool:
        return self._links.detach(question_id, source_id)

    def _hydrate(self, link: QuestionSource) -> LinkedSource:
        if (link.source.provider, link.source.scope) != ("zotero", "personal"):
            return LinkedSource(link, HydrationStatus.UNRESOLVED, error_code="unsupported_scope")
        try:
            item = _validate_item(
                self._zotero.get_item(link.source.external_key), link.source.external_key
            )
            return LinkedSource(link, HydrationStatus.RESOLVED, item)
        except ZoteroItemNotFoundError as exc:
            return LinkedSource(
                link,
                HydrationStatus.UNRESOLVED,
                error_code="item_deleted" if exc.deleted else "item_not_found",
            )
        except (ZoteroParseError, ValueError):
            return LinkedSource(link, HydrationStatus.ERROR, error_code="invalid_metadata")
        except (ZoteroCommandError, ZoteroCliNotFoundError, ZoteroUnavailableError):
            return LinkedSource(
                link, HydrationStatus.UNAVAILABLE, error_code="metadata_unavailable"
            )
        except ZoteroError:
            return LinkedSource(link, HydrationStatus.ERROR, error_code="zotero_error")
