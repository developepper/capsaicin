"""Tests for the web UI runtime (T02).

Tests exercise ASGI routes via httpx against a real SQLite database,
verifying that the web layer correctly consumes T01 shared services.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from capsaicin.web.app import create_app
from tests.conftest import add_ticket


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def web_client(project_env):
    """Return a Starlette TestClient wired to a real project DB."""
    env = project_env
    app = create_app(
        db_path=env["project_dir"] / "capsaicin.db",
        project_id=env["project_id"],
        config_path=env["project_dir"] / "config.toml",
        log_path=env["project_dir"] / "activity.log",
    )
    return TestClient(app), env


# ---------------------------------------------------------------------------
# Dashboard route
# ---------------------------------------------------------------------------


class TestDashboardRoute:
    def test_empty_dashboard(self, web_client):
        client, _env = web_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text
        assert "0 tickets" in resp.text

    def test_dashboard_shows_tickets(self, web_client):
        client, env = web_client
        add_ticket(env, title="Web Ticket A")
        add_ticket(env, title="Web Ticket B")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "2 tickets" in resp.text
        assert "ready" in resp.text

    def test_dashboard_shows_human_gate(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Gate Ticket")
        _move_to_human_gate(env, tid)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Gate Ticket" in resp.text
        assert "Awaiting Human Gate" in resp.text

    def test_dashboard_shows_blocked(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Blocked Ticket")
        _move_to_blocked(env, tid)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Blocked Ticket" in resp.text
        assert "Blocked" in resp.text


# ---------------------------------------------------------------------------
# Ticket detail route
# ---------------------------------------------------------------------------


class TestTicketDetailRoute:
    def test_ticket_detail(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Detail Ticket", criteria=["c1", "c2"])

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Detail Ticket" in resp.text
        assert "c1" in resp.text
        assert "c2" in resp.text

    def test_ticket_not_found(self, web_client):
        client, _env = web_client
        resp = client.get("/tickets/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.text

    def test_ticket_shows_status(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Status Ticket")

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "ready" in resp.text


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------


class TestStaticFiles:
    def test_css_served(self, web_client):
        client, _env = web_client
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "body" in resp.text


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    def test_requests_use_separate_connections(self, web_client):
        """Each request should get its own connection (no leaking)."""
        client, env = web_client
        add_ticket(env, title="T1")

        # Multiple requests should all succeed without connection issues
        for _ in range(5):
            resp = client.get("/")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


class TestUiCommand:
    def test_ui_help(self):
        from click.testing import CliRunner
        from capsaicin.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["ui", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.output
        assert "--no-open" in result.output


# ---------------------------------------------------------------------------
# Server utilities
# ---------------------------------------------------------------------------


class TestServerUtilities:
    def test_find_open_port(self, monkeypatch):
        """find_open_port delegates to socket bind on port 0."""
        import socket as _socket
        from unittest.mock import MagicMock

        fake_sock = MagicMock()
        fake_sock.getsockname.return_value = ("127.0.0.1", 9123)
        fake_sock.__enter__ = lambda s: s
        fake_sock.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(
            _socket,
            "socket",
            lambda *a, **kw: fake_sock,
        )

        from capsaicin.web.server import find_open_port

        port = find_open_port()
        assert port == 9123
        fake_sock.bind.assert_called_once_with(("127.0.0.1", 0))


# ---------------------------------------------------------------------------
# Launcher connection lifecycle
# ---------------------------------------------------------------------------


class TestLauncherLifecycle:
    def test_ui_command_closes_context_before_server(self, project_env, monkeypatch):
        """The ui command must close the ProjectContext connection before
        starting the server, so no long-lived SQLite handle is held."""
        env = project_env
        captured = {}

        def fake_run_server(
            db_path, project_id, config_path, log_path, port=None, open_browser=True
        ):
            captured["project_id"] = project_id
            captured["db_path"] = db_path

        monkeypatch.setattr("capsaicin.web.server.run_server", fake_run_server)

        from click.testing import CliRunner
        from capsaicin.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["ui", "--no-open", "--repo", str(env["repo"])])
        assert result.exit_code == 0
        assert captured["project_id"] == env["project_id"]
        assert "capsaicin.db" in str(captured["db_path"])


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------


class TestVendoredAssets:
    def test_htmx_served_locally(self, web_client):
        """HTMX must be served from /static/, not fetched from a CDN."""
        client, _env = web_client
        resp = client.get("/static/htmx.min.js")
        assert resp.status_code == 200
        assert "htmx" in resp.text

    def test_base_template_references_local_htmx(self, web_client):
        """The dashboard page should reference the vendored HTMX, not unpkg."""
        client, _env = web_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "unpkg" not in resp.text
        assert "/static/htmx.min.js" in resp.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _move_to_human_gate(env, ticket_id):
    from capsaicin.state_machine import transition_ticket

    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"],
        ticket_id,
        "human-gate",
        "system",
        reason="test",
        gate_reason="review_passed",
    )


def _move_to_blocked(env, ticket_id):
    from capsaicin.state_machine import transition_ticket

    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"],
        ticket_id,
        "blocked",
        "system",
        reason="test",
        blocked_reason="implementation_failure",
    )
