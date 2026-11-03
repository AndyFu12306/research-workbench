"""Public read-only Zotero adapter API."""

from .client import ZoteroClient
from .errors import (
    ZoteroCliNotFoundError,
    ZoteroCommandError,
    ZoteroError,
    ZoteroItemNotFoundError,
    ZoteroParseError,
    ZoteroTimeoutError,
    ZoteroUnavailableError,
)
from .models import (
    ComponentHealth,
    ZoteroAnnotation,
    ZoteroFulltext,
    ZoteroHealth,
    ZoteroItem,
)

__all__ = [
    "ComponentHealth",
    "ZoteroAnnotation",
    "ZoteroCliNotFoundError",
    "ZoteroClient",
    "ZoteroCommandError",
    "ZoteroError",
    "ZoteroFulltext",
    "ZoteroHealth",
    "ZoteroItem",
    "ZoteroItemNotFoundError",
    "ZoteroParseError",
    "ZoteroTimeoutError",
    "ZoteroUnavailableError",
]
