"""Tests for SSE live-update endpoints (T05).

SSE streaming endpoints run indefinitely, so integration tests verify:
- correct response headers and content type
- internal snapshot and event-format functions
- partial endpoints that SSE events trigger
- template integration (scripts and hx-trigger attributes)

The streaming generator logic is tested via unit tests against the
snapshot functions and event formatters rather than blocking on the
full ASGI stream.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from starlette.testclient import TestClient

from capsaicin.db import get_connection
from capsaicin.state_machine import transition_ticket
from capsaicin.web.app import create_app
from capsaicin.web.routes.events import (
    _dashboard_snapshot,
    _sse_comment,
    _sse_event,
    _ticket_snapshot,
)
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


def _move_to_human_gate(env, ticket_id):
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
# SSE event format helpers
# ---------------------------------------------------------------------------


class TestSseEventFormat:
    def test_sse_event_format(self):
        result = _sse_event("test", '{"key": "val"}')
        assert "event: test\n" in result
        assert 'data: {"key": "val"}\n' in result
        assert result.endswith("\n\n")

    def test_sse_event_empty_data(self):
        result = _sse_event("ping")
        assert "event: ping\n" in result
        assert "data: \n" in result
        assert result.endswith("\n\n")

    def test_sse_event_multiline_data(self):
        result = _sse_event("multi", "line1\nline2")
        assert "data: line1\n" in result
        assert "data: line2\n" in result

    def test_sse_comment_default(self):
        assert _sse_comment() == ": keepalive\n\n"

    def test_sse_comment_custom(self):
        assert _sse_comment("ping") == ": ping\n\n"


# ---------------------------------------------------------------------------
# Event loop key coverage
# ---------------------------------------------------------------------------


class TestEventLoopKeyCoverage:
    def test_event_loop_emits_all_snapshot_keys(self, project_env):
        """The SSE event loop must iterate every key from _dashboard_snapshot
        so that all sections receive live updates, not just polling fallback."""
        import ast
        import inspect
        from capsaicin.web.routes.events import dashboard_events, _dashboard_snapshot

        env = project_env
        snap_keys = set(_dashboard_snapshot(env["conn"], env["project_id"]).keys())

        # Extract the literal tuple from the event loop's for-loop
        source = inspect.getsource(dashboard_events)
        tree = ast.parse(source)
        emitted_keys: set[str] = set()
        for node in ast.walk(tree):
            # Look for: for key in ("orchestrator", "inbox", ...)
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                for elt in node.iter.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        emitted_keys.add(elt.value)

        assert snap_keys == emitted_keys, (
            f"Snapshot keys {snap_keys} != event loop keys {emitted_keys}. "
            "A snapshot key is computed but never emitted as an SSE event."
        )


# ---------------------------------------------------------------------------
# Dashboard snapshot
# ---------------------------------------------------------------------------


class TestDashboardSnapshot:
    def test_empty_project_snapshot(self, project_env):
        env = project_env
        snap = _dashboard_snapshot(env["conn"], env["project_id"])

        assert "orchestrator" in snap
        assert "inbox" in snap
        assert "queue" in snap
        assert "blocked" in snap
        assert "next_runnable" in snap
        assert "activity" in snap
        # No tickets yet
        assert snap["inbox"] == (0, None)
        assert snap["blocked"] == (0, None)

    def test_snapshot_changes_on_ticket_add(self, project_env):
        env = project_env
        snap1 = _dashboard_snapshot(env["conn"], env["project_id"])

        add_ticket(env, title="New Ticket")
        snap2 = _dashboard_snapshot(env["conn"], env["project_id"])

        # Queue should change (new ticket count)
        assert snap2["queue"] != snap1["queue"]

    def test_snapshot_changes_on_human_gate(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Gate Ticket")
        snap1 = _dashboard_snapshot(env["conn"], env["project_id"])

        _move_to_human_gate(env, tid)
        snap2 = _dashboard_snapshot(env["conn"], env["project_id"])

        # Inbox should change
        assert snap2["inbox"] != snap1["inbox"]
        # Queue should change (status changed)
        assert snap2["queue"] != snap1["queue"]

    def test_snapshot_changes_on_orchestrator_update(self, project_env):
        env = project_env
        snap1 = _dashboard_snapshot(env["conn"], env["project_id"])

        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'running', updated_at = datetime('now') "
            "WHERE project_id = ?",
            (env["project_id"],),
        )
        env["conn"].commit()

        snap2 = _dashboard_snapshot(env["conn"], env["project_id"])
        assert snap2["orchestrator"] != snap1["orchestrator"]

    def test_snapshot_changes_on_blocked(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Block Ticket")
        snap1 = _dashboard_snapshot(env["conn"], env["project_id"])

        _move_to_blocked(env, tid)
        snap2 = _dashboard_snapshot(env["conn"], env["project_id"])

        assert snap2["blocked"] != snap1["blocked"]

    def test_snapshot_changes_on_next_runnable(self, project_env):
        env = project_env
        snap1 = _dashboard_snapshot(env["conn"], env["project_id"])

        add_ticket(env, title="New Ready")
        snap2 = _dashboard_snapshot(env["conn"], env["project_id"])

        # next_runnable key should change when a new ready ticket appears
        assert snap2["next_runnable"] != snap1["next_runnable"]

    def test_snapshot_stable_when_unchanged(self, project_env):
        env = project_env
        add_ticket(env, title="Stable")

        snap1 = _dashboard_snapshot(env["conn"], env["project_id"])
        snap2 = _dashboard_snapshot(env["conn"], env["project_id"])

        assert snap1 == snap2

    def test_snapshot_activity_changes_on_run(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Run Ticket")
        snap1 = _dashboard_snapshot(env["conn"], env["project_id"])

        # Insert a fake agent run
        env["conn"].execute(
            "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
            "attempt_number, exit_status, prompt, run_request, started_at) "
            "VALUES ('run-1', ?, 'implementer', 'read-write', 1, 1, 'success', "
            "'p', '{}', datetime('now'))",
            (tid,),
        )
        env["conn"].commit()

        snap2 = _dashboard_snapshot(env["conn"], env["project_id"])
        assert snap2["activity"] != snap1["activity"]


# ---------------------------------------------------------------------------
# Ticket snapshot
# ---------------------------------------------------------------------------


class TestTicketSnapshot:
    def test_ticket_snapshot_basic(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Snap Ticket", criteria=["c1"])

        snap = _ticket_snapshot(env["conn"], tid)
        assert snap is not None
        assert "ticket" in snap
        assert "findings" in snap
        assert "criteria" in snap
        assert "runs" in snap

    def test_nonexistent_ticket(self, project_env):
        env = project_env
        snap = _ticket_snapshot(env["conn"], "nonexistent")
        assert snap is None

    def test_snapshot_changes_on_status_change(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Status Change")

        snap1 = _ticket_snapshot(env["conn"], tid)
        _move_to_human_gate(env, tid)
        snap2 = _ticket_snapshot(env["conn"], tid)

        assert snap2["ticket"] != snap1["ticket"]

    def test_snapshot_changes_on_findings(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Finding Ticket")

        # Need a run for findings FK
        env["conn"].execute(
            "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
            "attempt_number, exit_status, prompt, run_request, started_at) "
            "VALUES ('run-f1', ?, 'reviewer', 'read-only', 1, 1, 'success', "
            "'p', '{}', datetime('now'))",
            (tid,),
        )
        env["conn"].commit()

        snap1 = _ticket_snapshot(env["conn"], tid)

        env["conn"].execute(
            "INSERT INTO findings (id, run_id, ticket_id, severity, category, "
            "fingerprint, description, disposition) "
            "VALUES ('f1', 'run-f1', ?, 'blocking', 'correctness', 'fp1', "
            "'Missing test', 'open')",
            (tid,),
        )
        env["conn"].commit()

        snap2 = _ticket_snapshot(env["conn"], tid)
        assert snap2["findings"] != snap1["findings"]

    def test_snapshot_stable_when_unchanged(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Stable Ticket")

        snap1 = _ticket_snapshot(env["conn"], tid)
        snap2 = _ticket_snapshot(env["conn"], tid)
        assert snap1 == snap2


# ---------------------------------------------------------------------------
# New partial endpoints
# ---------------------------------------------------------------------------


class TestOrchestratorPartial:
    def test_returns_200(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/orchestrator")
        assert resp.status_code == 200

    def test_contains_orchestrator_status(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/orchestrator")
        assert "Orchestrator" in resp.text
        assert "idle" in resp.text

    def test_has_sse_trigger(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/orchestrator")
        assert "sse-orchestrator from:body" in resp.text

    def test_has_polling_fallback(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/orchestrator")
        assert "every 30s" in resp.text


class TestBlockedPartial:
    def test_returns_200(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/blocked")
        assert resp.status_code == 200

    def test_empty_state(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/blocked")
        assert "No blocked tickets" in resp.text

    def test_shows_blocked_ticket(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Blocked One")
        _move_to_blocked(env, tid)

        resp = client.get("/partials/blocked")
        assert "Blocked One" in resp.text
        assert "implementation_failure" in resp.text

    def test_has_sse_trigger(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/blocked")
        assert "sse-blocked from:body" in resp.text

    def test_has_polling_fallback(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/blocked")
        assert "every 30s" in resp.text


class TestNextRunnablePartial:
    def test_returns_200(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/next-runnable")
        assert resp.status_code == 200

    def test_empty_state(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/next-runnable")
        assert "No runnable tickets" in resp.text

    def test_shows_next_runnable(self, web_client):
        client, env = web_client
        add_ticket(env, title="Ready One")

        resp = client.get("/partials/next-runnable")
        assert "Ready One" in resp.text

    def test_has_sse_trigger(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/next-runnable")
        assert "sse-next_runnable from:body" in resp.text

    def test_has_polling_fallback(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/next-runnable")
        assert "every 30s" in resp.text


class TestTicketContentPartial:
    def test_returns_200(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Content Partial", criteria=["c1"])

        resp = client.get(f"/partials/tickets/{tid}")
        assert resp.status_code == 200

    def test_contains_ticket_data(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Data Partial", criteria=["criterion A"])

        resp = client.get(f"/partials/tickets/{tid}")
        assert "criterion A" in resp.text
        assert "ready" in resp.text

    def test_has_sse_trigger(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="SSE Partial")

        resp = client.get(f"/partials/tickets/{tid}")
        assert "sse-ticket from:body" in resp.text

    def test_not_found(self, web_client):
        client, _env = web_client
        resp = client.get("/partials/tickets/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dashboard template SSE integration
# ---------------------------------------------------------------------------


class TestDashboardSseIntegration:
    def test_has_eventsource_script(self, web_client):
        client, _env = web_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "EventSource" in resp.text
        assert "/events/dashboard" in resp.text

    def test_sections_have_sse_triggers(self, web_client):
        client, _env = web_client
        resp = client.get("/")
        assert "sse-inbox from:body" in resp.text
        assert "sse-queue from:body" in resp.text
        assert "sse-blocked from:body" in resp.text
        assert "sse-next_runnable from:body" in resp.text
        assert "sse-activity from:body" in resp.text

    def test_retains_polling_fallback(self, web_client):
        client, _env = web_client
        resp = client.get("/")
        # inbox, queue, blocked, next-runnable, activity (+ orchestrator)
        assert resp.text.count("every 30s") >= 5

    def test_script_dispatches_custom_events(self, web_client):
        """Script should dispatch sse-* CustomEvents on body."""
        client, _env = web_client
        resp = client.get("/")
        assert "CustomEvent" in resp.text
        assert "sse-" in resp.text


# ---------------------------------------------------------------------------
# Ticket detail template SSE integration
# ---------------------------------------------------------------------------


class TestTicketDetailSseIntegration:
    def test_has_eventsource_script(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="SSE Detail")

        resp = client.get(f"/tickets/{tid}")
        assert "EventSource" in resp.text
        assert f"/events/tickets/" in resp.text

    def test_content_has_sse_trigger(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="SSE Trigger")

        resp = client.get(f"/tickets/{tid}")
        assert "sse-ticket from:body" in resp.text

    def test_handles_ticket_gone_event(self, web_client):
        """Script should close EventSource on ticket-gone."""
        client, env = web_client
        tid = add_ticket(env, title="Gone Handler")

        resp = client.get(f"/tickets/{tid}")
        assert "ticket-gone" in resp.text
        assert "src.close()" in resp.text


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_dashboard_works_without_sse(self, web_client):
        """Dashboard renders and polls normally without SSE connected."""
        client, env = web_client
        add_ticket(env, title="No SSE Needed")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "every 30s" in resp.text

    def test_ticket_detail_works_without_sse(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Static Detail", criteria=["c1"])

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "c1" in resp.text

    def test_all_partials_work_independently(self, web_client):
        """Partial endpoints work for both SSE-triggered and polling refreshes."""
        client, env = web_client
        tid = add_ticket(env, title="Partial OK")

        for url in [
            "/partials/inbox",
            "/partials/queue",
            "/partials/blocked",
            "/partials/next-runnable",
            "/partials/activity",
            "/partials/orchestrator",
            f"/partials/tickets/{tid}",
        ]:
            resp = client.get(url)
            assert resp.status_code == 200

    def test_eventsource_check_in_script(self, web_client):
        """Script guards against missing EventSource API."""
        client, _env = web_client
        resp = client.get("/")
        assert "typeof EventSource" in resp.text


# ---------------------------------------------------------------------------
# Existing routes still work
# ---------------------------------------------------------------------------


class TestExistingRoutesUnbroken:
    def test_dashboard_still_renders(self, web_client):
        client, env = web_client
        add_ticket(env, title="Still Works")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text
        assert "1 ticket" in resp.text

    def test_ticket_detail_still_renders(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Detail Works", criteria=["c1", "c2"])

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Detail Works" in resp.text
        assert "c1" in resp.text

    def test_existing_partials_still_work(self, web_client):
        client, env = web_client
        add_ticket(env, title="Partials OK")

        for url in ["/partials/inbox", "/partials/queue", "/partials/activity"]:
            resp = client.get(url)
            assert resp.status_code == 200

    def test_static_files_still_served(self, web_client):
        client, _env = web_client
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
