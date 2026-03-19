"""Tests for the database module (T02)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from capsaicin.db import get_connection, run_migrations


class TestGetConnection:
    def test_returns_connection(self):
        conn = get_connection(":memory:")
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_foreign_keys_enabled(self):
        conn = get_connection(":memory:")
        result = conn.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1
        conn.close()

    def test_fk_violation_raises(self):
        conn = get_connection(":memory:")
        conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE child ("
            "  id TEXT PRIMARY KEY,"
            "  parent_id TEXT NOT NULL REFERENCES parent(id)"
            ")"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO child (id, parent_id) VALUES ('c1', 'nonexistent')"
            )
        conn.close()

    def test_row_factory_is_row(self):
        conn = get_connection(":memory:")
        assert conn.row_factory is sqlite3.Row
        conn.close()

    def test_file_based_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        assert db_path.exists()


class TestRunMigrations:
    def test_creates_version_table(self):
        conn = get_connection(":memory:")
        run_migrations(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "_migration_versions" in tables
        conn.close()

    def test_idempotent_no_migrations(self):
        conn = get_connection(":memory:")
        run_migrations(conn)
        run_migrations(conn)
        conn.close()

    def test_applies_migration_files(self, tmp_path):
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_create_demo.sql").write_text(
            "CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY, name TEXT NOT NULL);"
        )

        conn = get_connection(":memory:")
        with patch("capsaicin.db.MIGRATIONS_DIR", migrations_dir):
            run_migrations(conn)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "demo" in tables

        # Verify version was recorded
        versions = [
            row[0] for row in conn.execute("SELECT version FROM _migration_versions")
        ]
        assert "0001_create_demo" in versions
        conn.close()

    def test_idempotent_with_migrations(self, tmp_path):
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_create_demo.sql").write_text(
            "CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY);"
        )

        conn = get_connection(":memory:")
        with patch("capsaicin.db.MIGRATIONS_DIR", migrations_dir):
            run_migrations(conn)
            run_migrations(conn)

        # Should only have one version entry
        count = conn.execute(
            "SELECT COUNT(*) FROM _migration_versions"
        ).fetchone()[0]
        assert count == 1
        conn.close()

    def test_migrations_applied_in_order(self, tmp_path):
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_first.sql").write_text(
            "CREATE TABLE IF NOT EXISTS first_table (id TEXT PRIMARY KEY);"
        )
        (migrations_dir / "0002_second.sql").write_text(
            "CREATE TABLE IF NOT EXISTS second_table ("
            "  id TEXT PRIMARY KEY,"
            "  first_id TEXT REFERENCES first_table(id)"
            ");"
        )

        conn = get_connection(":memory:")
        with patch("capsaicin.db.MIGRATIONS_DIR", migrations_dir):
            run_migrations(conn)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "first_table" in tables
        assert "second_table" in tables

        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM _migration_versions ORDER BY version"
            )
        ]
        assert versions == ["0001_first", "0002_second"]
        conn.close()

    def test_new_migration_applied_on_second_call(self, tmp_path):
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_first.sql").write_text(
            "CREATE TABLE IF NOT EXISTS first_table (id TEXT PRIMARY KEY);"
        )

        conn = get_connection(":memory:")
        with patch("capsaicin.db.MIGRATIONS_DIR", migrations_dir):
            run_migrations(conn)

            # Add a second migration after the first run
            (migrations_dir / "0002_second.sql").write_text(
                "CREATE TABLE IF NOT EXISTS second_table (id TEXT PRIMARY KEY);"
            )
            run_migrations(conn)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "second_table" in tables

        count = conn.execute(
            "SELECT COUNT(*) FROM _migration_versions"
        ).fetchone()[0]
        assert count == 2
        conn.close()

    def test_failed_migration_leaves_no_partial_state(self, tmp_path):
        """A migration that fails mid-script must not leave partial DDL or a
        version row behind — the entire migration is atomic."""
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_bad.sql").write_text(
            "CREATE TABLE good_table (id TEXT PRIMARY KEY);\n"
            "THIS IS INVALID SQL;\n"
        )

        conn = get_connection(":memory:")
        with patch("capsaicin.db.MIGRATIONS_DIR", migrations_dir):
            with pytest.raises(sqlite3.OperationalError):
                run_migrations(conn)

        # Neither the table nor the version row should exist
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "good_table" not in tables

        count = conn.execute(
            "SELECT COUNT(*) FROM _migration_versions"
        ).fetchone()[0]
        assert count == 0

        conn.close()

    def test_connection_usable_after_failed_migration(self, tmp_path):
        """After a migration failure, the connection should still be usable."""
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_bad.sql").write_text("INVALID SQL;")

        conn = get_connection(":memory:")
        with patch("capsaicin.db.MIGRATIONS_DIR", migrations_dir):
            with pytest.raises(sqlite3.OperationalError):
                run_migrations(conn)

        # Connection is still healthy
        conn.execute("CREATE TABLE after_fail (id TEXT PRIMARY KEY)")
        conn.commit()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "after_fail" in tables
        conn.close()

    def test_no_migrations_dir_is_noop(self, tmp_path):
        nonexistent = tmp_path / "no_such_dir"
        conn = get_connection(":memory:")
        with patch("capsaicin.db.MIGRATIONS_DIR", nonexistent):
            run_migrations(conn)
        # Only the version table should exist
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"_migration_versions"}
        conn.close()
