"""Explicit SQLite online backup and append-only migration verification."""

import hashlib
import json
import sqlite3
import uuid


def _snapshot(connection):
    tables = [
        r[0]
        for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'alembic_version' ORDER BY name"
        )
    ]
    return {
        name: hashlib.sha256(
            json.dumps(
                sorted(
                    connection.execute(
                        'SELECT * FROM "' + name.replace('"', '""') + '"'
                    ).fetchall(),
                    key=repr,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        for name in tables
    }


def _check(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Database foreign key check failed")


def backup_and_upgrade(database, *, on_backup=None):
    """Never alter old rows; keep the backup even if upgrade/check fails."""
    backup_path = None
    before = {}
    if database.path.is_file():
        backup_path = database.path.with_name(
            database.path.stem + ".backup-" + uuid.uuid4().hex + ".db"
        )
        # Exclusive creation prevents overwriting an existing backup.
        with backup_path.open("xb"):
            pass
        with sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True) as source:
            _check(source)
            before = _snapshot(source)
            with sqlite3.connect(backup_path) as target:
                source.backup(target)
                _check(target)
                if _snapshot(target) != before:
                    raise ValueError("Backup verification failed")
        if on_backup is not None:
            on_backup(backup_path)
    database.upgrade()
    with sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True) as current:
        _check(current)
        after = _snapshot(current)
        if any(after.get(table) != digest for table, digest in before.items()):
            raise ValueError("Migration changed historical rows; verified backup retained")
    return backup_path
