"""Tests for T03 — dashboard read model and web routes.

Tests exercise the refined DashboardData model, HTMX partial endpoints,
and the full dashboard screen against a real SQLite database.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from capsaicin.app.queries.dashboard import (
    DashboardData,
    OrchestratorSummary,
    get_dashboard,
)
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
# Read model tests
# ---------------------------------------------------------------------------


class TestDashboardReadModel:
    def test_empty_project(self, project_env):
        env = project_env
        data = get_dashboard(env["conn"], env["project_id"])

        assert isinstance(data, DashboardData)
        assert data.total_tickets == 0
        assert data.counts_by_status == {}
        assert data.active_ticket is None
        assert data.human_gate_tickets == []
        assert data.blocked_tickets == []
        assert data.next_runnable is None

    def test_orchestrator_summary_present(self, project_env):
        env = project_env
        data = get_dashboard(env["conn"], env["project_id"])

        assert data.orchestrator is not None
        assert isinstance(data.orchestrator, OrchestratorSummary)
        assert data.orchestrator.status == "idle"
        assert data.orchestrator.active_ticket_id is None

    def test_inbox_summary_empty(self, project_env):
        env = project_env
        data = get_dashboard(env["conn"], env["project_id"])

        assert data.inbox is not None
        assert data.inbox.count == 0
        assert data.inbox.tickets == []

    def test_inbox_summary_with_gate_tickets(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Gate Item")
        _move_to_human_gate(env, tid)

        data = get_dashboard(env["conn"], env["project_id"])

        assert data.inbox.count == 1
        assert data.inbox.tickets[0]["title"] == "Gate Item"
        assert data.inbox.tickets[0]["gate_reason"] == "review_passed"

    def test_recent_runs_empty(self, project_env):
        env = project_env
        data = get_dashboard(env["conn"], env["project_id"])

        assert data.recent_runs == []

    def test_recent_runs_populated(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Run Ticket")
        _insert_fake_run(env, tid)

        data = get_dashboard(env["conn"], env["project_id"])

        assert len(data.recent_runs) == 1
        assert data.recent_runs[0]["ticket_title"] == "Run Ticket"
        assert data.recent_runs[0]["role"] == "implementer"

    def test_next_runnable_with_tickets(self, project_env):
        env = project_env
        add_ticket(env, title="Runnable A")

        data = get_dashboard(env["conn"], env["project_id"])

        assert data.next_runnable is not None
        assert data.next_runnable["title"] == "Runnable A"

    def test_blocked_summary(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Stuck Ticket")
        _move_to_blocked(env, tid)

        data = get_dashboard(env["conn"], env["project_id"])

        assert len(data.blocked_tickets) == 1
        assert data.blocked_tickets[0]["title"] == "Stuck Ticket"


# ---------------------------------------------------------------------------
# Dashboard route tests
# ---------------------------------------------------------------------------


class TestDashboardRoute:
    def test_full_dashboard_renders(self, web_client):
        client, _env = web_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text
        assert "Orchestrator" in resp.text
        assert "Inbox" in resp.text
        assert "Queue" in resp.text
        assert "Recent Activity" in resp.text

    def test_dashboard_empty(self, web_client):
        client, _env = web_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "0 tickets" in resp.text
        assert "No tickets awaiting human action" in resp.text

    def test_dashboard_with_tickets(self, web_client):
        client, env = web_client
        add_ticket(env, title="Alpha")
        add_ticket(env, title="Beta")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "2 tickets" in resp.text
        assert "ready" in resp.text

    def test_dashboard_shows_inbox(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Human Decision")
        _move_to_human_gate(env, tid)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Human Decision" in resp.text
        assert "review_passed" in resp.text

    def test_dashboard_shows_blocked(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Blocked Work")
        _move_to_blocked(env, tid)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Blocked Work" in resp.text

    def test_dashboard_shows_next_runnable(self, web_client):
        client, env = web_client
        add_ticket(env, title="Ready To Go")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Ready To Go" in resp.text

    def test_dashboard_shows_orchestrator_idle(self, web_client):
        client, _env = web_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "idle" in resp.text

    def test_dashboard_shows_recent_activity(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Activity Ticket")
        _insert_fake_run(env, tid)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Activity Ticket" in resp.text
        assert "implementer" in resp.text


# ---------------------------------------------------------------------------
# HTMX partial routes
# ---------------------------------------------------------------------------


class TestPartialRoutes:
    def test_partial_inbox(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Partial Gate")
        _move_to_human_gate(env, tid)

        resp = client.get("/partials/inbox")
        assert resp.status_code == 200
        assert "Partial Gate" in resp.text
        assert "hx-get" in resp.text

    def test_partial_inbox_empty(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/inbox")
        assert resp.status_code == 200
        assert "No tickets awaiting human action" in resp.text

    def test_partial_queue(self, web_client):
        client, env = web_client
        add_ticket(env, title="Q1")
        add_ticket(env, title="Q2")

        resp = client.get("/partials/queue")
        assert resp.status_code == 200
        assert "2 tickets" in resp.text
        assert "ready" in resp.text

    def test_partial_activity(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Act Ticket")
        _insert_fake_run(env, tid)

        resp = client.get("/partials/activity")
        assert resp.status_code == 200
        assert "Act Ticket" in resp.text

    def test_partial_activity_empty(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/activity")
        assert resp.status_code == 200
        assert "No runs yet" in resp.text

    def test_partials_use_same_read_model_as_dashboard(self, web_client):
        """Partials should produce the same HTML fragment as the full page."""
        client, env = web_client
        add_ticket(env, title="Consistency Check")

        full = client.get("/")
        partial = client.get("/partials/queue")

        # Both should contain the queue section with the same data
        assert "Consistency Check" not in partial.text  # not in queue table
        assert "1 ticket" in full.text
        assert "1 ticket" in partial.text


# ---------------------------------------------------------------------------
# Workspace state on dashboard (AC-1)
# ---------------------------------------------------------------------------


class TestDashboardWorkspaceState:
    def test_blocked_tickets_show_workspace_column(self, web_client):
        """Dashboard blocked table includes Workspace column header."""
        client, env = web_client
        tid = add_ticket(env, title="Blocked WS")
        _move_to_blocked(env, tid)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Workspace" in resp.text

    def test_shared_mode_shows_shared(self, web_client):
        """When workspace isolation is disabled, blocked tickets show 'shared'."""
        client, env = web_client
        tid = add_ticket(env, title="Shared Blocked")
        _move_to_blocked(env, tid)

        resp = client.get("/")
        assert resp.status_code == 200
        # The workspace column should show "shared" when isolation is disabled
        assert "shared" in resp.text

    def test_workspace_enabled_shows_isolation_mode_and_status(self, web_client):
        """With workspace enabled, blocked tickets show isolation mode and status."""
        client, env = web_client
        tid = add_ticket(env, title="WS Blocked")
        _move_to_blocked(env, tid)

        _enable_workspace(env)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "WS Blocked" in resp.text
        # Workspace column should include the status category (e.g. "none" when
        # no workspace record exists yet) via the workspace-status CSS class.
        assert "workspace-status" in resp.text

    def test_inbox_tickets_show_workspace_column(self, web_client):
        """Dashboard inbox table includes Workspace column header."""
        client, env = web_client
        tid = add_ticket(env, title="Gate WS")
        _move_to_human_gate(env, tid)

        resp = client.get("/")
        assert resp.status_code == 200
        # Inbox table should have the Workspace column
        assert "Gate WS" in resp.text
        # Should show "shared" when workspace isolation is disabled
        assert "shared" in resp.text

    def test_inbox_workspace_enabled(self, web_client):
        """With workspace enabled, inbox tickets show workspace status."""
        client, env = web_client
        tid = add_ticket(env, title="Gate WS Enabled")
        _move_to_human_gate(env, tid)

        _enable_workspace(env)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Gate WS Enabled" in resp.text
        assert "workspace-status" in resp.text

    def test_partial_inbox_shows_workspace(self, web_client):
        """The inbox partial includes workspace summaries."""
        client, env = web_client
        tid = add_ticket(env, title="Partial Gate WS")
        _move_to_human_gate(env, tid)

        resp = client.get("/partials/inbox")
        assert resp.status_code == 200
        assert "Partial Gate WS" in resp.text
        assert "shared" in resp.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enable_workspace(env):
    """Enable workspace isolation in the project config file."""
    config_path = env["project_dir"] / "config.toml"
    text = config_path.read_text()
    if "[workspace]" not in text:
        text += '\n[workspace]\nenabled = true\nbranch_prefix = "capsaicin/"\nauto_cleanup = true\n'
    else:
        text = text.replace("enabled = false", "enabled = true")
    config_path.write_text(text)


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


def _insert_fake_run(env, ticket_id):
    """Insert a minimal agent_runs record for testing."""
    from capsaicin.queries import generate_id, now_utc

    run_id = generate_id()
    env["conn"].execute(
        "INSERT INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, started_at, finished_at, "
        "duration_seconds) "
        "VALUES (?, ?, 'implementer', 'read-write', 1, 1, "
        "'success', 'test prompt', '{}', ?, ?, 5.0)",
        (run_id, ticket_id, now_utc(), now_utc()),
    )
    env["conn"].commit()
    return run_id
