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
