"""Alembic environment shared by CLI and Database.upgrade()."""

from alembic import context
from sqlalchemy import URL, inspect

from research_workbench.persistence import (
    analysis_models,  # noqa: F401 -- register analysis mappings
    source_models,  # noqa: F401 -- register source mappings
)
from research_workbench.persistence.config import resolve_database_path
from research_workbench.persistence.database import Database
from research_workbench.persistence.errors import MigrationError
from research_workbench.persistence.models import Base


def run_with_connection(connection):
    tables = set(inspect(connection).get_table_names())
    if tables and "alembic_version" not in tables:
        raise MigrationError(
            "Refusing to migrate an unversioned database with existing tables. "
            "Choose an empty Workbench database; "
            "do not point this at Zotero or another application's data."
        )
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=URL.create("sqlite+pysqlite", database=str(resolve_database_path())),
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := context.config.attributes.get("connection")) is not None:
    run_with_connection(connection)
else:
    with Database() as database, database._engine.begin() as connection:
        run_with_connection(connection)
