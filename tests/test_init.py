"""Tests for capsaicin init (T05)."""

from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from capsaicin.cli import cli
from capsaicin.db import get_connection
from capsaicin.init import init_project, slugify


class TestSlugify:
    def test_basic(self):
        assert slugify("My Project") == "my-project"

    def test_strips_special_chars(self):
        assert slugify("Project @#$ Name!") == "project-name"

    def test_collapses_hyphens(self):
        assert slugify("a - - b") == "a-b"

    def test_strips_leading_trailing(self):
        assert slugify("  --hello--  ") == "hello"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty slug"):
            init_project("@#$!", "/tmp/fakerepo")


class TestInitProject:
    def test_creates_directory_structure(self, tmp_path):
        project_dir = init_project("test-proj", str(tmp_path))
        assert project_dir.exists()
        assert (project_dir / "capsaicin.db").exists()
        assert (project_dir / "config.toml").exists()
        assert (project_dir / "activity.log").exists()
        assert (project_dir / "renders").is_dir()
        assert (project_dir / "renders" / "epics").is_dir()
        assert (project_dir / "renders" / "tickets").is_dir()
        assert (project_dir / "renders" / "reviews").is_dir()
        assert (project_dir / "exports" / "github" / "issues").is_dir()
        assert (project_dir / "exports" / "github" / "prs").is_dir()

    def test_db_has_all_tables(self, tmp_path):
        project_dir = init_project("test-proj", str(tmp_path))
        conn = get_connection(project_dir / "capsaicin.db")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND substr(name,1,1) != '_' AND name != 'sqlite_sequence'"
            )
        }
        assert "projects" in tables
        assert "tickets" in tables
        assert "orchestrator_state" in tables
        conn.close()

    def test_projects_row_exists(self, tmp_path):
        project_dir = init_project("test-proj", str(tmp_path))
        conn = get_connection(project_dir / "capsaicin.db")
        row = conn.execute("SELECT * FROM projects").fetchone()
        assert row is not None
        assert row["name"] == "test-proj"
        assert row["repo_path"] == str(tmp_path.resolve())
        conn.close()

    def test_config_snapshot_complete(self, tmp_path):
        """DB snapshot must reflect the full parsed config, not a subset."""
        project_dir = init_project("test-proj", str(tmp_path))
        conn = get_connection(project_dir / "capsaicin.db")
        row = conn.execute("SELECT config FROM projects").fetchone()
        snapshot = json.loads(row["config"])
        # All top-level sections present
        for section in (
            "project",
            "adapters",
            "limits",
            "reviewer",
            "ticket_selection",
            "paths",
        ):
            assert section in snapshot, f"Missing section: {section}"
        # Adapter fields include optional model and allowed_tools
        assert "model" in snapshot["adapters"]["implementer"]
        assert "allowed_tools" in snapshot["adapters"]["reviewer"]
        # Verify specific values match defaults
        assert snapshot["reviewer"]["mode"] == "read-only"
        assert snapshot["ticket_selection"]["order"] == "created_at"
        assert snapshot["paths"]["renders_dir"] == "renders"
        assert snapshot["paths"]["exports_dir"] == "exports"
        assert snapshot["limits"]["timeout_seconds"] == 300
        conn.close()

    def test_orchestrator_state_idle(self, tmp_path):
        project_dir = init_project("test-proj", str(tmp_path))
        conn = get_connection(project_dir / "capsaicin.db")
        row = conn.execute("SELECT * FROM orchestrator_state").fetchone()
        assert row is not None
        assert row["status"] == "idle"
        conn.close()

    def test_config_has_absolute_repo_path(self, tmp_path):
        project_dir = init_project("test-proj", str(tmp_path))
        config_text = (project_dir / "config.toml").read_text()
        assert str(tmp_path.resolve()) in config_text

    def test_activity_log_has_init_event(self, tmp_path):
        project_dir = init_project("test-proj", str(tmp_path))
        log_text = (project_dir / "activity.log").read_text()
        assert "PROJECT_INIT" in log_text
        assert "project_id=" in log_text

    def test_duplicate_project_errors(self, tmp_path):
        init_project("test-proj", str(tmp_path))
        with pytest.raises(ValueError, match="already exists"):
            init_project("test-proj", str(tmp_path))

    def test_repo_path_resolved_to_absolute(self, tmp_path):
        project_dir = init_project("test-proj", str(tmp_path / "." / "subdir" / ".."))
        conn = get_connection(project_dir / "capsaicin.db")
        row = conn.execute("SELECT repo_path FROM projects").fetchone()
        assert not row["repo_path"].endswith("..")
        assert "/" in row["repo_path"]  # absolute path
        conn.close()


class TestInitCLI:
    def test_init_via_cli(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["init", "--project", "cli-proj", "--repo", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Initialized project" in result.output
        assert (
            tmp_path / ".capsaicin" / "projects" / "cli-proj" / "capsaicin.db"
        ).exists()

    def test_init_default_project_name(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / ".capsaicin" / "projects" / "my-project").is_dir()

    def test_init_duplicate_errors(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "dup", "--repo", str(tmp_path)])
        result = runner.invoke(
            cli, ["init", "--project", "dup", "--repo", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_help_shows_init(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        assert "Initialize" in result.output
