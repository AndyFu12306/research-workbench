"""Immutable values returned by repositories, never live SQLAlchemy sessions."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ResearchStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    title: str
    description: str
    status: ResearchStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Question:
    id: int
    project_id: int
    title: str
    description: str
    status: ResearchStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourceRef:
    id: int
    provider: str
    scope: str
    external_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class QuestionSource:
    question_id: int
    source: SourceRef
    created_at: datetime

    @property
    def source_id(self) -> int:
        return self.source.id
