"""Immutable runtime DTOs. None of these objects is a metadata database."""

from dataclasses import dataclass
from typing import Literal

HealthStatus = Literal["healthy", "degraded", "unavailable"]


@dataclass(frozen=True, slots=True)
class ZoteroItem:
    item_key: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    item_type: str
    publication_date: str | None


@dataclass(frozen=True, slots=True)
class ZoteroFulltext:
    item_key: str
    text: str
    page_count: int | None = None


@dataclass(frozen=True, slots=True)
class ZoteroAnnotation:
    annotation_key: str | None
    item_key: str | None
    attachment_key: str | None
    annotation_type: str | None
    text: str
    comment: str
    page: str | None
    page_index: int | None


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    status: HealthStatus
    detail: str


@dataclass(frozen=True, slots=True)
class ZoteroHealth:
    status: HealthStatus
    client: ComponentHealth
    local_api: ComponentHealth
    cli: ComponentHealth
