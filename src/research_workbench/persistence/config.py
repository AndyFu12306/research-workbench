"""Local SQLite file configuration; no global state or implicit .env loading."""

import os
from collections.abc import Mapping
from pathlib import Path


def resolve_database_path(
    path: str | Path | None = None, *, environ: Mapping[str, str] | None = None
) -> Path:
    """Explicit path > WORKBENCH_DATABASE_PATH > cwd/data/research_workbench.db."""
    env = os.environ if environ is None else environ
    value = path if path is not None else env.get("WORKBENCH_DATABASE_PATH") or None
    if value is None:
        value = Path.cwd() / "data" / "research_workbench.db"
    if not str(value).strip() or str(value) == ":memory:" or "://" in str(value):
        raise ValueError("Configure a SQLite file path, not an empty value, memory DB or URL.")
    resolved = Path(value).expanduser().resolve()
    if resolved.is_dir():
        raise ValueError("Database path names a directory; specify a SQLite file.")
    return resolved
