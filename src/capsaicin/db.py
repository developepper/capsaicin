"""SQLite connection factory and migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Return a SQLite connection with foreign-key enforcement enabled.

    Args:
        db_path: Path to the SQLite database file, or ":memory:" for in-memory.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    """Execute all migration SQL files in order. Idempotent via a version table.

    Migration files are named ``NNNN_description.sql`` and executed in sorted
    order.  Each file is applied at most once, tracked by the
    ``_migration_versions`` table.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migration_versions ("
        "  version TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )

    if not MIGRATIONS_DIR.is_dir():
        return

    applied: set[str] = {
        row[0] for row in conn.execute("SELECT version FROM _migration_versions")
    }

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = sql_file.stem
        if version in applied:
            continue
        sql = sql_file.read_text()
        # Wrap migration SQL and version tracking in a single transaction so
        # a partial failure doesn't leave the schema half-applied with no
        # version row.  executescript auto-commits, so we avoid it and bundle
        # the version INSERT into the same atomic script.
        safe_version = version.replace("'", "''")
        atomic_sql = (
            f"BEGIN;\n{sql}\n"
            f"INSERT INTO _migration_versions (version) "
            f"VALUES ('{safe_version}');\n"
            f"COMMIT;"
        )
        try:
            conn.executescript(atomic_sql)
        except Exception:
            # executescript leaves the txn open on error — roll it back so
            # the connection is usable and no partial DDL is committed.
            conn.execute("ROLLBACK")
            raise
