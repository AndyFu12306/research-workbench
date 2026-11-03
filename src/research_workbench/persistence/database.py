"""SQLite lifecycle and session boundary; schema changes go through Alembic."""

import math
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import URL, create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .config import resolve_database_path
from .errors import MigrationError, PersistenceError


class Database:
    """A local Workbench DB, independent of Zotero. Call upgrade() explicitly.

    No schema is created on import/instantiation. Each repository operation
    owns one session/transaction; no session escapes to callers.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        timeout: float = 5,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("SQLite busy timeout must be positive and finite.")
        self.path = resolve_database_path(path, environ=environ)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.path)),
            connect_args={"autocommit": False, "timeout": timeout},
        )

        @event.listens_for(self._engine, "connect")
        def enable_foreign_keys(connection, _record):
            # sqlite3 autocommit=False already starts a transaction; PRAGMA needs to run outside it.
            previous = connection.autocommit
            connection.autocommit = True
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                finally:
                    cursor.close()
            finally:
                connection.autocommit = previous

        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    @contextmanager
    def _session(self) -> Iterator[Session]:
        try:
            with self._sessions.begin() as session:
                yield session
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Workbench database operation failed and was rolled back. "
                "Check that Database.upgrade() has initialized the schema and the file is writable."
            ) from exc

    def upgrade(self) -> None:
        """Apply packaged Alembic migrations. Never call metadata.create_all()."""
        config = Config()
        scripts = Path(__file__).with_name("migrations")
        # ConfigParser interpolates percent signs; actual filesystem paths may contain them.
        config.set_main_option("script_location", str(scripts).replace("%", "%%"))
        try:
            with self._engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        except (CommandError, SQLAlchemyError) as exc:
            raise MigrationError(
                "Workbench schema upgrade failed; no repository operation was run."
            ) from exc

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
