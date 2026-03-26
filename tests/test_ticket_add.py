"""Tests for capsaicin ticket add (T06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from capsaicin.cli import cli
from capsaicin.db import get_connection, run_migrations
from capsaicin.init import init_project
from capsaicin.ticket_add import (
    _get_project_id,
    add_ticket_from_file,
    add_ticket_inline,
)


@pytest.fixture
def project(tmp_path):
    """Initialize a project and return (project_dir, conn, project_id)."""
    project_dir = init_project("test-proj", str(tmp_path))
    conn = get_connection(project_dir / "capsaicin.db")
    project_id = _get_project_id(conn)
    yield project_dir, conn, project_id
    conn.close()


class TestAddTicketInline:
    def test_creates_ticket(self, project):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        ticket_id = add_ticket_inline(
            conn, project_id, "My Title", "My Desc", [], log_path
        )
        row = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        assert row["title"] == "My Title"
        assert row["description"] == "My Desc"
        assert row["status"] == "ready"
        assert row["project_id"] == project_id

    def test_creates_criteria(self, project):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        ticket_id = add_ticket_inline(
            conn, project_id, "T", "D", ["crit1", "crit2"], log_path
        )
        rows = conn.execute(
            "SELECT * FROM acceptance_criteria WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["description"] == "crit1"
        assert rows[0]["status"] == "pending"
        assert rows[1]["description"] == "crit2"

    def test_records_state_transition(self, project):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        ticket_id = add_ticket_inline(conn, project_id, "T", "D", [], log_path)
        row = conn.execute(
            "SELECT * FROM state_transitions WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        assert row is not None
        assert row["from_status"] == "null"
        assert row["to_status"] == "ready"
        assert row["triggered_by"] == "human"

    def test_logs_event(self, project):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        ticket_id = add_ticket_inline(conn, project_id, "T", "D", [], log_path)
        log_text = log_path.read_text()
        assert "TICKET_CREATED" in log_text
        assert ticket_id in log_text

    def test_ulid_format(self, project):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        ticket_id = add_ticket_inline(conn, project_id, "T", "D", ["c"], log_path)
        # ULID is 26 chars, uppercase alphanumeric
        assert len(ticket_id) == 26
        criterion = conn.execute(
            "SELECT id FROM acceptance_criteria WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        assert len(criterion["id"]) == 26

    def test_no_criteria_is_valid(self, project):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        ticket_id = add_ticket_inline(conn, project_id, "T", "D", [], log_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM acceptance_criteria WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()[0]
        assert count == 0


class TestAddTicketFromFile:
    def test_parses_toml(self, project, tmp_path):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        toml_file = tmp_path / "ticket.toml"
        toml_file.write_text(
            'title = "Auth ticket"\n'
            'description = """\nAdd JWT auth.\n"""\n\n'
            '[[criteria]]\ndescription = "Login returns JWT"\n\n'
            '[[criteria]]\ndescription = "Expired tokens rejected"\n'
        )
        ticket_id = add_ticket_from_file(conn, project_id, toml_file, log_path)
        row = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        assert row["title"] == "Auth ticket"
        assert "JWT" in row["description"]
        criteria = conn.execute(
            "SELECT description FROM acceptance_criteria WHERE ticket_id = ? ORDER BY rowid",
            (ticket_id,),
        ).fetchall()
        assert len(criteria) == 2
        assert criteria[0]["description"] == "Login returns JWT"

    def test_missing_title_errors(self, project, tmp_path):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text('description = "no title"\n')
        with pytest.raises(ValueError, match="title"):
            add_ticket_from_file(conn, project_id, toml_file, log_path)

    def test_missing_description_errors(self, project, tmp_path):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text('title = "no desc"\n')
        with pytest.raises(ValueError, match="description"):
            add_ticket_from_file(conn, project_id, toml_file, log_path)

    def test_criterion_missing_description_errors(self, project, tmp_path):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text('title = "T"\ndescription = "D"\n\n[[criteria]]\n')
        with pytest.raises(ValueError, match="Criterion 1.*missing.*description"):
            add_ticket_from_file(conn, project_id, toml_file, log_path)

    def test_no_criteria_section(self, project, tmp_path):
        project_dir, conn, project_id = project
        log_path = project_dir / "activity.log"
        toml_file = tmp_path / "minimal.toml"
        toml_file.write_text('title = "T"\ndescription = "D"\n')
        ticket_id = add_ticket_from_file(conn, project_id, toml_file, log_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM acceptance_criteria WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()[0]
        assert count == 0


class TestTicketAddCLI:
    def test_inline_add(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "p", "--repo", str(tmp_path)])
        result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "My Ticket",
                "--description",
                "Do stuff",
                "--criteria",
                "It works",
                "--criteria",
                "It's fast",
                "--repo",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "My Ticket" in result.output
        assert "Criteria: 2" in result.output

    def test_from_file(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "p", "--repo", str(tmp_path)])
        toml_file = tmp_path / "ticket.toml"
        toml_file.write_text(
            'title = "File Ticket"\ndescription = "From file"\n'
            '[[criteria]]\ndescription = "It works"\n'
        )
        result = runner.invoke(
            cli,
            ["ticket", "add", "--from", str(toml_file), "--repo", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "File Ticket" in result.output
        assert "Criteria: 1" in result.output

    def test_no_title_no_from_errors(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "p", "--repo", str(tmp_path)])
        result = runner.invoke(cli, ["ticket", "add", "--repo", str(tmp_path)])
        assert result.exit_code != 0
        assert "--title" in result.output or "--from" in result.output

    def test_title_without_description_errors(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "p", "--repo", str(tmp_path)])
        result = runner.invoke(
            cli, ["ticket", "add", "--title", "T", "--repo", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "description" in result.output.lower()

    def test_both_title_and_from_errors(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "p", "--repo", str(tmp_path)])
        toml_file = tmp_path / "t.toml"
        toml_file.write_text('title = "T"\ndescription = "D"\n')
        result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "X",
                "--from",
                str(toml_file),
                "--repo",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0

    def test_project_flag_selects_project(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "proj-a", "--repo", str(tmp_path)])
        runner.invoke(cli, ["init", "--project", "proj-b", "--repo", str(tmp_path)])
        # Without --project, should fail due to ambiguity
        result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "T",
                "--description",
                "D",
                "--repo",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0
        assert "Multiple" in result.output
        # With --project, should succeed
        result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "T",
                "--description",
                "D",
                "--repo",
                str(tmp_path),
                "--project",
                "proj-a",
            ],
        )
        assert result.exit_code == 0
        assert "T" in result.output

    def test_project_flag_invalid_slug_errors(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "p", "--repo", str(tmp_path)])
        result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "T",
                "--description",
                "D",
                "--repo",
                str(tmp_path),
                "--project",
                "nonexistent",
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_ticket_id_printed(self, tmp_path):
        runner = CliRunner()
        runner.invoke(cli, ["init", "--project", "p", "--repo", str(tmp_path)])
        result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "T",
                "--description",
                "D",
                "--repo",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        # Output should contain a 26-char ULID on the "Ticket" line
        for line in result.output.splitlines():
            if line.startswith("Ticket "):
                ulid_str = line.split(" ", 1)[1].strip()
                assert len(ulid_str) == 26
                break
        else:
            pytest.fail("No 'Ticket <ULID>' line in output")
