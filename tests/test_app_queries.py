"""Tests for app.queries — structured read models."""

from __future__ import annotations

import json

import pytest

from capsaicin.app.queries.activity import RecentRun, get_recent_activity
from capsaicin.app.queries.dashboard import DashboardData, get_dashboard
from capsaicin.app.queries.diagnostics import RunDiagnostic, get_run_diagnostic
from capsaicin.app.queries.inbox import InboxItem, get_inbox
from capsaicin.app.queries.ticket_detail import TicketDetailData, get_ticket_detail
from capsaicin.ticket_status import render_dashboard, render_ticket_detail
from tests.conftest import add_ticket


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_empty_project(self, project_env):
        data = get_dashboard(project_env["conn"], project_env["project_id"])
        assert isinstance(data, DashboardData)
        assert data.total_tickets == 0
        assert data.counts_by_status == {}
        assert data.active_ticket is None
        assert data.human_gate_tickets == []
        assert data.blocked_tickets == []
        assert data.next_runnable is None

    def test_with_tickets(self, project_env):
        env = project_env
        add_ticket(env, title="Ticket A")
        add_ticket(env, title="Ticket B")

        data = get_dashboard(env["conn"], env["project_id"])
        assert data.total_tickets == 2
        assert data.counts_by_status.get("ready") == 2
        assert data.next_runnable is not None
        assert data.next_runnable["title"] in ("Ticket A", "Ticket B")

    def test_human_gate_tickets(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Gate ticket")
        _move_to_human_gate(env, tid)

        data = get_dashboard(env["conn"], env["project_id"])
        assert len(data.human_gate_tickets) == 1
        assert data.human_gate_tickets[0]["id"] == tid

    def test_blocked_tickets(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Blocked ticket")
        _move_to_blocked(env, tid)

        data = get_dashboard(env["conn"], env["project_id"])
        assert len(data.blocked_tickets) == 1


# ---------------------------------------------------------------------------
# render_dashboard produces same output as build_project_summary
# ---------------------------------------------------------------------------


class TestRenderDashboard:
    def test_render_matches_legacy(self, project_env):
        from capsaicin.ticket_status import build_project_summary

        env = project_env
        add_ticket(env, title="T1")
        add_ticket(env, title="T2")

        legacy = build_project_summary(env["conn"], env["project_id"])
        rendered = render_dashboard(env["conn"], env["project_id"])

        assert rendered == legacy


# ---------------------------------------------------------------------------
# Ticket Detail
# ---------------------------------------------------------------------------


class TestTicketDetail:
    def test_basic_detail(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Detail test", criteria=["c1", "c2"])

        data = get_ticket_detail(env["conn"], tid)
        assert isinstance(data, TicketDetailData)
        assert data.ticket["id"] == tid
        assert data.ticket["title"] == "Detail test"
        assert len(data.criteria) == 2
        assert data.last_run is None
        assert data.run_history is None
        assert data.transition_history is None

    def test_verbose_includes_history(self, project_env):
        env = project_env
        tid = add_ticket(env)

        data = get_ticket_detail(env["conn"], tid, verbose=True)
        # Should be lists (possibly empty) not None
        assert data.run_history is not None
        assert data.transition_history is not None

    def test_not_found_raises(self, project_env):
        with pytest.raises(ValueError, match="not found"):
            get_ticket_detail(project_env["conn"], "nonexistent")


# ---------------------------------------------------------------------------
# render_ticket_detail matches legacy build_ticket_detail
# ---------------------------------------------------------------------------


class TestRenderTicketDetail:
    def test_render_matches_legacy(self, project_env):
        from capsaicin.ticket_status import build_ticket_detail

        env = project_env
        tid = add_ticket(env, title="Render test", criteria=["c1"])

        legacy = build_ticket_detail(env["conn"], tid)
        rendered = render_ticket_detail(env["conn"], tid)

        assert rendered == legacy

    def test_render_verbose_matches_legacy(self, project_env):
        from capsaicin.ticket_status import build_ticket_detail

        env = project_env
        tid = add_ticket(env, title="Verbose test")

        legacy = build_ticket_detail(env["conn"], tid, verbose=True)
        rendered = render_ticket_detail(env["conn"], tid, verbose=True)

        assert rendered == legacy


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


class TestInbox:
    def test_empty_inbox(self, project_env):
        items = get_inbox(project_env["conn"], project_env["project_id"])
        assert items == []

    def test_inbox_with_gate_ticket(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Gate ticket", criteria=["c1"])
        _move_to_human_gate(env, tid)

        items = get_inbox(env["conn"], env["project_id"])
        assert len(items) == 1
        item = items[0]
        assert isinstance(item, InboxItem)
        assert item.ticket_id == tid
        assert item.title == "Gate ticket"
        assert item.gate_reason == "review_passed"
        assert len(item.criteria) == 1
        assert item.criteria[0]["description"] == "c1"


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


class TestActivity:
    def test_empty_activity(self, project_env):
        runs = get_recent_activity(project_env["conn"], project_env["project_id"])
        assert runs == []

    def test_activity_with_run(self, project_env):
        env = project_env
        tid = add_ticket(env)

        # Insert a synthetic run
        from capsaicin.queries import generate_id, now_utc

        run_id = generate_id()
        env["conn"].execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, attempt_number, "
            "exit_status, prompt, run_request, started_at, finished_at) "
            "VALUES (?, ?, 'implementer', 'read-write', 1, 1, 'success', "
            "'test', '{}', ?, ?)",
            (run_id, tid, now_utc(), now_utc()),
        )
        env["conn"].commit()

        runs = get_recent_activity(env["conn"], env["project_id"])
        assert len(runs) == 1
        r = runs[0]
        assert isinstance(r, RecentRun)
        assert r.run_id == run_id
        assert r.ticket_id == tid
        assert r.role == "implementer"

    def test_activity_limit(self, project_env):
        env = project_env
        tid = add_ticket(env)

        from capsaicin.queries import generate_id, now_utc

        for _ in range(5):
            rid = generate_id()
            env["conn"].execute(
                "INSERT INTO agent_runs "
                "(id, ticket_id, role, mode, cycle_number, attempt_number, "
                "exit_status, prompt, run_request, started_at) "
                "VALUES (?, ?, 'implementer', 'read-write', 1, 1, 'success', "
                "'test', '{}', ?)",
                (rid, tid, now_utc()),
            )
        env["conn"].commit()

        runs = get_recent_activity(env["conn"], env["project_id"], limit=3)
        assert len(runs) == 3


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


class TestDiagnostics:
    def test_no_runs(self, project_env):
        env = project_env
        tid = add_ticket(env)

        diag = get_run_diagnostic(env["conn"], tid)
        assert diag is None

    def test_with_run(self, project_env):
        env = project_env
        tid = add_ticket(env)

        from capsaicin.queries import generate_id, now_utc

        run_id = generate_id()
        raw = json.dumps({"result": "I made changes"})
        env["conn"].execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, attempt_number, "
            "exit_status, prompt, run_request, raw_stdout, started_at, finished_at) "
            "VALUES (?, ?, 'implementer', 'read-write', 1, 1, 'success', "
            "'test', '{}', ?, ?, ?)",
            (run_id, tid, raw, now_utc(), now_utc()),
        )
        env["conn"].commit()

        diag = get_run_diagnostic(env["conn"], tid)
        assert isinstance(diag, RunDiagnostic)
        assert diag.run_id == run_id
        assert diag.role == "implementer"
        assert diag.exit_status == "success"
        assert diag.agent_text == "I made changes"

    def test_specific_run_id(self, project_env):
        env = project_env
        tid = add_ticket(env)

        from capsaicin.queries import generate_id, now_utc

        run_id = generate_id()
        env["conn"].execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, attempt_number, "
            "exit_status, prompt, run_request, started_at) "
            "VALUES (?, ?, 'implementer', 'read-write', 1, 1, 'failure', "
            "'test', '{}', ?)",
            (run_id, tid, now_utc()),
        )
        env["conn"].commit()

        diag = get_run_diagnostic(env["conn"], tid, run_id=run_id)
        assert diag is not None
        assert diag.exit_status == "failure"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _move_to_human_gate(env, ticket_id):
    from capsaicin.orchestrator import await_human
    from capsaicin.state_machine import transition_ticket

    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"], ticket_id, "human-gate", "system",
        reason="test", gate_reason="review_passed",
    )
    await_human(env["conn"], env["project_id"])


def _move_to_blocked(env, ticket_id):
    from capsaicin.state_machine import transition_ticket

    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"], ticket_id, "blocked", "system",
        reason="test", blocked_reason="implementation_failure",
    )
