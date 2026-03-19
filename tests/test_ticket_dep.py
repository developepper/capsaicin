"""Tests for capsaicin ticket dep (T07)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from capsaicin.cli import cli
from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.ticket_add import _get_project_id, add_ticket_inline
from capsaicin.ticket_dep import add_dependency


@pytest.fixture
def project(tmp_path):
    """Initialize a project and return (project_dir, conn, project_id)."""
    project_dir = init_project("test-proj", str(tmp_path))
    conn = get_connection(project_dir / "capsaicin.db")
    project_id = _get_project_id(conn)
    yield project_dir, conn, project_id
    conn.close()


def _add(conn, project_id, log_path, title="T"):
    return add_ticket_inline(conn, project_id, title, "D", [], log_path)


class TestAddDependency:
    def test_valid_dependency(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t1 = _add(conn, project_id, log, "A")
        t2 = _add(conn, project_id, log, "B")
        add_dependency(conn, t1, t2)
        row = conn.execute(
            "SELECT * FROM ticket_dependencies WHERE ticket_id = ? AND depends_on_id = ?",
            (t1, t2),
        ).fetchone()
        assert row is not None

    def test_self_dependency_rejected(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t1 = _add(conn, project_id, log)
        with pytest.raises(ValueError, match="cannot depend on itself"):
            add_dependency(conn, t1, t1)

    def test_nonexistent_ticket_errors(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t1 = _add(conn, project_id, log)
        with pytest.raises(ValueError, match="not found"):
            add_dependency(conn, t1, "NONEXISTENT")

    def test_nonexistent_source_errors(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t1 = _add(conn, project_id, log)
        with pytest.raises(ValueError, match="not found"):
            add_dependency(conn, "NONEXISTENT", t1)

    def test_duplicate_is_idempotent(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t1 = _add(conn, project_id, log, "A")
        t2 = _add(conn, project_id, log, "B")
        add_dependency(conn, t1, t2)
        add_dependency(conn, t1, t2)  # no error
        count = conn.execute(
            "SELECT COUNT(*) FROM ticket_dependencies WHERE ticket_id = ? AND depends_on_id = ?",
            (t1, t2),
        ).fetchone()[0]
        assert count == 1

    def test_simple_cycle_rejected(self, project):
        """A -> B -> A should be rejected."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t1 = _add(conn, project_id, log, "A")
        t2 = _add(conn, project_id, log, "B")
        add_dependency(conn, t1, t2)
        with pytest.raises(ValueError, match="cycle"):
            add_dependency(conn, t2, t1)

    def test_transitive_cycle_rejected(self, project):
        """A -> B -> C -> A should be rejected."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t1 = _add(conn, project_id, log, "A")
        t2 = _add(conn, project_id, log, "B")
        t3 = _add(conn, project_id, log, "C")
        add_dependency(conn, t1, t2)
        add_dependency(conn, t2, t3)
        with pytest.raises(ValueError, match="cycle"):
            add_dependency(conn, t3, t1)

    def test_non_cycle_chain_allowed(self, project):
        """A -> B -> C is fine (no cycle)."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t1 = _add(conn, project_id, log, "A")
        t2 = _add(conn, project_id, log, "B")
        t3 = _add(conn, project_id, log, "C")
        add_dependency(conn, t1, t2)
        add_dependency(conn, t2, t3)
        count = conn.execute("SELECT COUNT(*) FROM ticket_dependencies").fetchone()[0]
        assert count == 2

    def test_diamond_dependency_allowed(self, project):
        """A -> B, A -> C, B -> D, C -> D is fine (diamond, no cycle)."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        a = _add(conn, project_id, log, "A")
        b = _add(conn, project_id, log, "B")
        c = _add(conn, project_id, log, "C")
        d = _add(conn, project_id, log, "D")
        add_dependency(conn, a, b)
        add_dependency(conn, a, c)
        add_dependency(conn, b, d)
        add_dependency(conn, c, d)
        count = conn.execute("SELECT COUNT(*) FROM ticket_dependencies").fetchone()[0]
        assert count == 4


class TestTicketDepCLI:
    def _setup(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "p", "--repo", str(tmp_path)])
        r1 = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "A",
                "--description",
                "D",
                "--repo",
                str(tmp_path),
            ],
        )
        r2 = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "B",
                "--description",
                "D",
                "--repo",
                str(tmp_path),
            ],
        )
        t1 = self._extract_id(r1.output)
        t2 = self._extract_id(r2.output)
        return runner, t1, t2

    @staticmethod
    def _extract_id(output):
        for line in output.splitlines():
            if line.startswith("Ticket "):
                return line.split(" ", 1)[1].strip()
        raise AssertionError("No ticket ID found in output")

    def test_dep_via_cli(self, tmp_path):
        runner, t1, t2 = self._setup(tmp_path)
        result = runner.invoke(
            cli, ["ticket", "dep", t1, "--on", t2, "--repo", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Dependency added" in result.output

    def test_dep_self_errors(self, tmp_path):
        runner, t1, _ = self._setup(tmp_path)
        result = runner.invoke(
            cli, ["ticket", "dep", t1, "--on", t1, "--repo", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "itself" in result.output

    def test_dep_nonexistent_errors(self, tmp_path):
        runner, t1, _ = self._setup(tmp_path)
        result = runner.invoke(
            cli, ["ticket", "dep", t1, "--on", "FAKE", "--repo", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_dep_cycle_errors(self, tmp_path):
        runner, t1, t2 = self._setup(tmp_path)
        runner.invoke(cli, ["ticket", "dep", t1, "--on", t2, "--repo", str(tmp_path)])
        result = runner.invoke(
            cli, ["ticket", "dep", t2, "--on", t1, "--repo", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "cycle" in result.output
