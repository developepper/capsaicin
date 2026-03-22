"""Tests for T07 — packaging, partials coverage, and end-to-end operator flows.

Covers:
- templates and static assets are accessible at runtime
- all partial endpoints return valid HTML
- end-to-end operator flow: approve, revise-then-approve, defer, unblock
- capsaicin ui command help and launch
"""

from __future__ import annotations

import pathlib

import pytest
from starlette.testclient import TestClient

from capsaicin.state_machine import transition_ticket
from capsaicin.web.app import create_app
from tests.conftest import add_ticket


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def web_client(project_env):
    env = project_env
    app = create_app(
        db_path=env["project_dir"] / "capsaicin.db",
        project_id=env["project_id"],
        config_path=env["project_dir"] / "config.toml",
        log_path=env["project_dir"] / "activity.log",
    )
    return TestClient(app), env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _move_to_human_gate(env, ticket_id, gate_reason="review_passed"):
    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"],
        ticket_id,
        "human-gate",
        "system",
        reason="test",
        gate_reason=gate_reason,
    )


def _move_to_blocked(env, ticket_id):
    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"],
        ticket_id,
        "blocked",
        "system",
        reason="test",
        blocked_reason="implementation_failure",
    )


# ---------------------------------------------------------------------------
# Packaging: templates and static assets accessible at runtime
# ---------------------------------------------------------------------------


class TestPackaging:
    """Verify that templates and static assets are accessible from the
    *installed* package, not just the source tree.

    These tests resolve paths through ``capsaicin.web.__file__`` — the same
    mechanism the Jinja2 template loader and StaticFiles mount use at
    runtime.  If a wheel or sdist omits a file, these fail even though
    the source tree still has the file on disk.
    """

    @staticmethod
    def _pkg_dir() -> pathlib.Path:
        """Return the installed web package directory."""
        import capsaicin.web as w

        return pathlib.Path(w.__file__).parent

    def test_templates_directory_accessible(self):
        assert (self._pkg_dir() / "templates").is_dir()

    def test_static_directory_accessible(self):
        assert (self._pkg_dir() / "static").is_dir()

    def test_base_template_accessible(self):
        assert (self._pkg_dir() / "templates" / "base.html").is_file()

    def test_dashboard_template_accessible(self):
        assert (self._pkg_dir() / "templates" / "dashboard.html").is_file()

    def test_ticket_detail_template_accessible(self):
        assert (self._pkg_dir() / "templates" / "ticket_detail.html").is_file()

    def test_all_partial_templates_accessible(self):
        partials = self._pkg_dir() / "templates" / "partials"
        expected = [
            "inbox.html",
            "queue.html",
            "activity.html",
            "blocked.html",
            "next_runnable.html",
            "orchestrator.html",
            "ticket_content.html",
        ]
        for name in expected:
            assert (partials / name).is_file(), f"Missing partial: {name}"

    def test_htmx_vendored_accessible(self):
        assert (self._pkg_dir() / "static" / "htmx.min.js").is_file()

    def test_stylesheet_accessible(self):
        assert (self._pkg_dir() / "static" / "style.css").is_file()

    def test_template_loader_resolves_to_installed_package(self):
        """The Jinja2 template loader must point at the installed package
        directory, not a hardcoded source-tree path."""
        from capsaicin.web.templating import templates as tmpl

        search_paths = tmpl.env.loader.searchpath
        assert len(search_paths) == 1
        resolved = pathlib.Path(search_paths[0])
        assert resolved == self._pkg_dir() / "templates"

    def test_static_mount_resolves_to_installed_package(self):
        """The create_app StaticFiles mount must serve from the installed
        package directory."""
        from capsaicin.web.app import _STATIC_DIR

        assert pathlib.Path(_STATIC_DIR) == self._pkg_dir() / "static"

    def test_css_served_via_app(self, web_client):
        client, _env = web_client
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "action-panel" in resp.text  # T06 CSS

    def test_htmx_served_via_app(self, web_client):
        client, _env = web_client
        resp = client.get("/static/htmx.min.js")
        assert resp.status_code == 200

    def test_dashboard_renders_from_installed_templates(self, web_client):
        """Full integration: the dashboard renders correctly, proving
        templates are loadable from the installed package path."""
        client, _env = web_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text
        # base.html inclusion
        assert "/static/htmx.min.js" in resp.text


# ---------------------------------------------------------------------------
# Partials endpoints — all return valid HTML
# ---------------------------------------------------------------------------


class TestPartials:
    def test_partial_inbox_empty(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/inbox")
        assert resp.status_code == 200
        assert "Inbox" in resp.text

    def test_partial_inbox_with_tickets(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Inbox Partial")
        _move_to_human_gate(env, tid)

        resp = client.get("/partials/inbox")
        assert resp.status_code == 200
        assert "Inbox Partial" in resp.text

    def test_partial_queue_empty(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/queue")
        assert resp.status_code == 200
        assert "Queue" in resp.text

    def test_partial_queue_with_tickets(self, web_client):
        client, env = web_client
        add_ticket(env, title="Q1")
        add_ticket(env, title="Q2")

        resp = client.get("/partials/queue")
        assert resp.status_code == 200
        assert "ready" in resp.text

    def test_partial_activity_empty(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/activity")
        assert resp.status_code == 200
        assert "Recent Activity" in resp.text or "No runs" in resp.text

    def test_partial_blocked_empty(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/blocked")
        assert resp.status_code == 200
        assert "Blocked" in resp.text

    def test_partial_blocked_with_tickets(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Blocked Partial")
        _move_to_blocked(env, tid)

        resp = client.get("/partials/blocked")
        assert resp.status_code == 200
        assert "Blocked Partial" in resp.text

    def test_partial_next_runnable_empty(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/next-runnable")
        assert resp.status_code == 200

    def test_partial_next_runnable_with_ticket(self, web_client):
        client, env = web_client
        add_ticket(env, title="Next Runnable")

        resp = client.get("/partials/next-runnable")
        assert resp.status_code == 200
        assert "Next Runnable" in resp.text

    def test_partial_orchestrator(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/orchestrator")
        assert resp.status_code == 200
        assert "Orchestrator" in resp.text
        assert "idle" in resp.text

    def test_partial_ticket_content(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Content Partial", criteria=["c1"])

        resp = client.get(f"/partials/tickets/{tid}")
        assert resp.status_code == 200
        assert "Content Partial" not in resp.text  # title is in parent
        assert "ready" in resp.text
        assert "c1" in resp.text

    def test_partial_ticket_content_not_found(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/tickets/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# End-to-end operator flows via web actions
# ---------------------------------------------------------------------------


class TestEndToEndFlows:
    def test_approve_flow(self, web_client):
        """Full flow: ready -> human-gate -> approve -> pr-ready."""
        client, env = web_client
        tid = add_ticket(env, title="E2E Approve", criteria=["criterion"])
        _move_to_human_gate(env, tid)

        # Verify human-gate shows action forms
        resp = client.get(f"/tickets/{tid}")
        assert "Human Decision Required" in resp.text
        assert "Approve" in resp.text

        # Approve
        resp = client.post(
            f"/tickets/{tid}/approve",
            data={"rationale": "Reviewed"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "pr-ready" in resp.text
        # No more action forms
        assert "Human Decision Required" not in resp.text

    def test_revise_then_approve_flow(self, web_client):
        """Full flow: human-gate -> revise -> (manual re-gate) -> approve."""
        client, env = web_client
        tid = add_ticket(env, title="E2E Revise", criteria=["criterion"])
        _move_to_human_gate(env, tid)

        # Revise with a finding
        client.post(
            f"/tickets/{tid}/revise",
            data={"finding": "Add more tests"},
        )

        # Verify ticket is in revise
        resp = client.get(f"/tickets/{tid}")
        assert "revise" in resp.text
        assert "Run Implementation" in resp.text

        # Verify finding was persisted
        findings = (
            env["conn"]
            .execute(
                "SELECT description FROM findings "
                "WHERE ticket_id = ? AND category = 'human_feedback'",
                (tid,),
            )
            .fetchall()
        )
        assert len(findings) == 1
        assert findings[0]["description"] == "Add more tests"

        # Move back to human-gate for approval (simulating re-run)
        transition_ticket(env["conn"], tid, "implementing", "system", reason="re-run")
        transition_ticket(
            env["conn"],
            tid,
            "human-gate",
            "system",
            reason="re-review passed",
            gate_reason="review_passed",
        )

        # Approve
        client.post(f"/tickets/{tid}/approve", data={})

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "pr-ready"

    def test_defer_flow(self, web_client):
        """Full flow: human-gate -> defer -> blocked."""
        client, env = web_client
        tid = add_ticket(env, title="E2E Defer")
        _move_to_human_gate(env, tid)

        client.post(
            f"/tickets/{tid}/defer",
            data={"rationale": "Needs more info"},
        )

        resp = client.get(f"/tickets/{tid}")
        assert "blocked" in resp.text
        assert "Unblock" in resp.text

    def test_defer_abandon_flow(self, web_client):
        """Full flow: human-gate -> abandon -> done."""
        client, env = web_client
        tid = add_ticket(env, title="E2E Abandon")
        _move_to_human_gate(env, tid)

        client.post(
            f"/tickets/{tid}/defer",
            data={"rationale": "Not needed", "abandon": "on"},
        )

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "done"

    def test_unblock_flow(self, web_client):
        """Full flow: blocked -> unblock -> ready."""
        client, env = web_client
        tid = add_ticket(env, title="E2E Unblock")
        _move_to_blocked(env, tid)

        # Verify blocked shows unblock button
        resp = client.get(f"/tickets/{tid}")
        assert "Unblock" in resp.text

        # Unblock
        client.post(f"/tickets/{tid}/unblock", data={})

        resp = client.get(f"/tickets/{tid}")
        assert "ready" in resp.text
        assert "Run Implementation" in resp.text

    def test_full_cycle_approve_revise_approve(self, web_client):
        """Two-cycle flow exercising multiple decisions on one ticket."""
        client, env = web_client
        tid = add_ticket(env, title="E2E Multi-Decision")
        _move_to_human_gate(env, tid)

        # First decision: revise
        client.post(f"/tickets/{tid}/revise", data={"finding": "Fix edge case"})

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "revise"

        # Simulate re-implementation and re-review
        transition_ticket(env["conn"], tid, "implementing", "system", reason="re-run")
        transition_ticket(
            env["conn"],
            tid,
            "human-gate",
            "system",
            reason="review passed",
            gate_reason="review_passed",
        )

        # Second decision: approve
        client.post(f"/tickets/{tid}/approve", data={"rationale": "Fixed"})

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "pr-ready"

        # Verify both decisions recorded
        decisions = (
            env["conn"]
            .execute(
                "SELECT decision FROM decisions WHERE ticket_id = ? ORDER BY created_at",
                (tid,),
            )
            .fetchall()
        )
        assert [d["decision"] for d in decisions] == ["revise", "approve"]


# ---------------------------------------------------------------------------
# CLI command coverage
# ---------------------------------------------------------------------------


class TestUiCommand:
    def test_ui_help_shows_options(self):
        from click.testing import CliRunner
        from capsaicin.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["ui", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.output
        assert "--no-open" in result.output

    def test_ui_command_exists_in_cli_group(self):
        from capsaicin.cli import cli

        commands = cli.list_commands(None)
        assert "ui" in commands

    def test_ui_resolves_context_and_launches(self, project_env, monkeypatch):
        """capsaicin ui resolves the project, then passes context to
        run_server with the correct db_path, project_id, and paths."""
        env = project_env
        captured = {}

        def fake_run_server(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("capsaicin.web.server.run_server", fake_run_server)

        from click.testing import CliRunner
        from capsaicin.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["ui", "--no-open", "--repo", str(env["repo"])])
        assert result.exit_code == 0

        assert captured["project_id"] == env["project_id"]
        assert "capsaicin.db" in str(captured["db_path"])
        assert "config.toml" in str(captured["config_path"])
        assert "activity.log" in str(captured["log_path"])
        assert captured["open_browser"] is False

    def test_ui_port_forwarded(self, project_env, monkeypatch):
        """--port is forwarded to run_server."""
        env = project_env
        captured = {}

        def fake_run_server(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("capsaicin.web.server.run_server", fake_run_server)

        from click.testing import CliRunner
        from capsaicin.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["ui", "--port", "9876", "--no-open", "--repo", str(env["repo"])]
        )
        assert result.exit_code == 0
        assert captured["port"] == 9876

    def test_ui_default_opens_browser(self, project_env, monkeypatch):
        """Without --no-open, open_browser defaults to True."""
        env = project_env
        captured = {}

        def fake_run_server(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("capsaicin.web.server.run_server", fake_run_server)

        from click.testing import CliRunner
        from capsaicin.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["ui", "--repo", str(env["repo"])])
        assert result.exit_code == 0
        assert captured["open_browser"] is True


# ---------------------------------------------------------------------------
# Dependencies declared in pyproject.toml
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_python_multipart_declared(self):
        """python-multipart must be declared for form parsing."""
        content = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
        assert "python-multipart" in content

    def test_starlette_declared(self):
        content = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
        assert "starlette" in content

    def test_jinja2_declared(self):
        content = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
        assert "jinja2" in content

    def test_uvicorn_declared(self):
        content = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
        assert "uvicorn" in content

    def test_package_data_includes_templates(self):
        content = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
        assert "web/templates" in content

    def test_package_data_includes_static(self):
        content = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
        assert "web/static" in content
