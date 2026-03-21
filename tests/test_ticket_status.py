"""Tests for the status command (T25)."""

from __future__ import annotations

import pytest

from capsaicin.ticket_status import (
    build_project_summary,
    build_ticket_detail,
    get_active_ticket,
    get_blocked_tickets,
    get_human_gate_tickets,
    get_next_runnable_ticket,
    get_ticket_counts_by_status,
)
from tests.conftest import add_ticket


# ---------------------------------------------------------------------------
# Ticket counts by status
# ---------------------------------------------------------------------------


class TestTicketCountsByStatus:
    def test_empty_project(self, project_env):
        counts = get_ticket_counts_by_status(
            project_env["conn"], project_env["project_id"]
        )
        assert counts == {}

    def test_counts_single_status(self, project_env):
        add_ticket(project_env, criteria=["criterion 1"])
        add_ticket(project_env, title="Second", criteria=["criterion 1"])
        counts = get_ticket_counts_by_status(
            project_env["conn"], project_env["project_id"]
        )
        assert counts == {"ready": 2}

    def test_counts_multiple_statuses(self, project_env):
        env = project_env
        t1 = add_ticket(env, title="T1", criteria=["criterion 1"])
        t2 = add_ticket(env, title="T2", criteria=["criterion 1"])
        t3 = add_ticket(env, title="T3", criteria=["criterion 1"])

        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', gate_reason = 'review_passed' WHERE id = ?",
            (t2,),
        )
        env["conn"].execute(
            "UPDATE tickets SET status = 'blocked', blocked_reason = 'test' WHERE id = ?",
            (t3,),
        )
        env["conn"].commit()

        counts = get_ticket_counts_by_status(env["conn"], env["project_id"])
        assert counts["ready"] == 1
        assert counts["human-gate"] == 1
        assert counts["blocked"] == 1


# ---------------------------------------------------------------------------
# Active ticket
# ---------------------------------------------------------------------------


class TestActiveTicket:
    def test_no_active_ticket(self, project_env):
        result = get_active_ticket(project_env["conn"], project_env["project_id"])
        assert result is None

    def test_active_ticket(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        env["conn"].execute(
            "UPDATE orchestrator_state SET active_ticket_id = ? WHERE project_id = ?",
            (tid, env["project_id"]),
        )
        env["conn"].commit()

        result = get_active_ticket(env["conn"], env["project_id"])
        assert result is not None
        assert result["id"] == tid


# ---------------------------------------------------------------------------
# Human-gate tickets
# ---------------------------------------------------------------------------


class TestHumanGateTickets:
    def test_no_gate_tickets(self, project_env):
        result = get_human_gate_tickets(project_env["conn"], project_env["project_id"])
        assert result == []

    def test_gate_tickets_with_reason(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', gate_reason = 'review_passed' WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()

        result = get_human_gate_tickets(env["conn"], env["project_id"])
        assert len(result) == 1
        assert result[0]["gate_reason"] == "review_passed"


# ---------------------------------------------------------------------------
# Blocked tickets
# ---------------------------------------------------------------------------


class TestBlockedTickets:
    def test_no_blocked_tickets(self, project_env):
        result = get_blocked_tickets(project_env["conn"], project_env["project_id"])
        assert result == []

    def test_blocked_with_reason(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        env["conn"].execute(
            "UPDATE tickets SET status = 'blocked', blocked_reason = 'impl failure' WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()

        result = get_blocked_tickets(env["conn"], env["project_id"])
        assert len(result) == 1
        assert result[0]["blocked_reason"] == "impl failure"


# ---------------------------------------------------------------------------
# Next runnable ticket
# ---------------------------------------------------------------------------


class TestNextRunnableTicket:
    def test_no_ready_tickets(self, project_env):
        result = get_next_runnable_ticket(
            project_env["conn"], project_env["project_id"]
        )
        assert result is None

    def test_ready_ticket_no_deps(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        result = get_next_runnable_ticket(env["conn"], env["project_id"])
        assert result is not None
        assert result["id"] == tid

    def test_respects_dependency_order(self, project_env):
        env = project_env
        t1 = add_ticket(env, title="First", criteria=["criterion 1"])
        t2 = add_ticket(env, title="Second", criteria=["criterion 1"])

        # t2 depends on t1
        env["conn"].execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (t2, t1),
        )
        env["conn"].commit()

        # t1 is ready, t2 depends on t1 (not done) -> next runnable is t1
        result = get_next_runnable_ticket(env["conn"], env["project_id"])
        assert result["id"] == t1

    def test_skips_unmet_deps(self, project_env):
        env = project_env
        t1 = add_ticket(env, title="First", criteria=["criterion 1"])
        t2 = add_ticket(env, title="Second", criteria=["criterion 1"])

        # t2 depends on t1; mark t1 as implementing
        env["conn"].execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (t2, t1),
        )
        env["conn"].execute(
            "UPDATE tickets SET status = 'implementing' WHERE id = ?", (t1,)
        )
        env["conn"].commit()

        # Only t2 is ready, but its dep isn't done -> no runnable
        result = get_next_runnable_ticket(env["conn"], env["project_id"])
        assert result is None

    def test_dep_satisfied_when_done(self, project_env):
        env = project_env
        t1 = add_ticket(env, title="First", criteria=["criterion 1"])
        t2 = add_ticket(env, title="Second", criteria=["criterion 1"])

        env["conn"].execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (t2, t1),
        )
        env["conn"].execute("UPDATE tickets SET status = 'done' WHERE id = ?", (t1,))
        env["conn"].commit()

        result = get_next_runnable_ticket(env["conn"], env["project_id"])
        assert result["id"] == t2


# ---------------------------------------------------------------------------
# Project summary
# ---------------------------------------------------------------------------


class TestBuildProjectSummary:
    def test_empty_project(self, project_env):
        output = build_project_summary(project_env["conn"], project_env["project_id"])
        assert "0 tickets" in output
        assert "No tickets" in output
        assert "Active Ticket: (none)" in output
        assert "Next Runnable: (none)" in output

    def test_with_tickets(self, project_env):
        env = project_env
        add_ticket(env, title="Ready One", criteria=["criterion 1"])
        t2 = add_ticket(env, title="Gated", criteria=["criterion 1"])
        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', gate_reason = 'review_passed' WHERE id = ?",
            (t2,),
        )
        env["conn"].commit()

        output = build_project_summary(env["conn"], env["project_id"])
        assert "2 tickets" in output
        assert "ready: 1" in output
        assert "human-gate: 1" in output
        assert "Awaiting Human Gate:" in output
        assert "review_passed" in output
        assert "Next Runnable:" in output

    def test_shows_blocked(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Stuck", criteria=["criterion 1"])
        env["conn"].execute(
            "UPDATE tickets SET status = 'blocked', blocked_reason = 'adapter crash' WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()

        output = build_project_summary(env["conn"], env["project_id"])
        assert "Blocked:" in output
        assert "adapter crash" in output

    def test_shows_active_ticket(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Active One", criteria=["criterion 1"])
        env["conn"].execute(
            "UPDATE orchestrator_state SET active_ticket_id = ? WHERE project_id = ?",
            (tid, env["project_id"]),
        )
        env["conn"].commit()

        output = build_project_summary(env["conn"], env["project_id"])
        assert "Active One" in output


# ---------------------------------------------------------------------------
# Ticket detail
# ---------------------------------------------------------------------------


class TestBuildTicketDetail:
    def test_not_found(self, project_env):
        with pytest.raises(ValueError, match="not found"):
            build_ticket_detail(project_env["conn"], "nonexistent")

    def test_basic_fields(self, project_env):
        env = project_env
        tid = add_ticket(env, title="My Ticket", criteria=["AC one", "AC two"])
        output = build_ticket_detail(env["conn"], tid)

        assert "My Ticket" in output
        assert "ready" in output
        assert "Status Changed:" in output
        assert "Cycle:" in output
        assert "[pending] AC one" in output
        assert "[pending] AC two" in output
        assert "Open Findings:" in output
        assert "Last Run: (none)" in output

    def test_shows_findings_grouped_by_severity(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])

        # Insert a synthetic run
        env["conn"].execute(
            "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
            "attempt_number, exit_status, prompt, run_request, started_at) "
            "VALUES ('run1', ?, 'reviewer', 'read-only', 1, 1, 'success', "
            "'p', '{}', datetime('now'))",
            (tid,),
        )
        # Insert findings
        env["conn"].execute(
            "INSERT INTO findings (id, run_id, ticket_id, severity, category, "
            "fingerprint, description, disposition) "
            "VALUES ('f1', 'run1', ?, 'blocking', 'correctness', 'fp1', "
            "'Missing null check', 'open')",
            (tid,),
        )
        env["conn"].execute(
            "INSERT INTO findings (id, run_id, ticket_id, severity, category, "
            "fingerprint, description, disposition) "
            "VALUES ('f2', 'run1', ?, 'warning', 'style', 'fp2', "
            "'Inconsistent naming', 'open')",
            (tid,),
        )
        env["conn"].commit()

        output = build_ticket_detail(env["conn"], tid)
        assert "blocking:" in output
        assert "Missing null check" in output
        assert "warning:" in output
        assert "Inconsistent naming" in output

    def test_shows_last_run(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])

        env["conn"].execute(
            "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
            "attempt_number, exit_status, verdict, duration_seconds, prompt, "
            "run_request, started_at, finished_at) "
            "VALUES ('run1', ?, 'reviewer', 'read-only', 1, 1, 'success', "
            "'pass', 12.5, 'p', '{}', datetime('now'), datetime('now'))",
            (tid,),
        )
        env["conn"].commit()

        output = build_ticket_detail(env["conn"], tid)
        assert "Role: reviewer" in output
        assert "Exit Status: success" in output
        assert "Duration: 12.5s" in output
        assert "Verdict: pass" in output

    def test_verbose_includes_run_history(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])

        env["conn"].execute(
            "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
            "attempt_number, exit_status, prompt, run_request, started_at) "
            "VALUES ('run1', ?, 'implementer', 'read-write', 1, 1, 'success', "
            "'p', '{}', '2025-01-01T00:00:00Z')",
            (tid,),
        )
        env["conn"].execute(
            "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
            "attempt_number, exit_status, verdict, prompt, run_request, started_at) "
            "VALUES ('run2', ?, 'reviewer', 'read-only', 1, 1, 'success', "
            "'pass', 'p', '{}', '2025-01-01T01:00:00Z')",
            (tid,),
        )
        env["conn"].commit()

        # Without verbose: no Run History section
        output = build_ticket_detail(env["conn"], tid, verbose=False)
        assert "Run History:" not in output

        # With verbose: includes Run History
        output = build_ticket_detail(env["conn"], tid, verbose=True)
        assert "Run History:" in output
        assert "implementer" in output
        assert "reviewer" in output

    def test_verbose_includes_transition_history(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])

        # The ticket add already inserts a null->ready transition
        output = build_ticket_detail(env["conn"], tid, verbose=True)
        assert "Transition History:" in output
        assert "-> ready" in output

    def test_gate_reason_shown(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', gate_reason = 'cycle_limit' WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()

        output = build_ticket_detail(env["conn"], tid)
        assert "Gate Reason: cycle_limit" in output

    def test_blocked_reason_shown(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        env["conn"].execute(
            "UPDATE tickets SET status = 'blocked', blocked_reason = 'adapter timeout' WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()

        output = build_ticket_detail(env["conn"], tid)
        assert "Blocked Reason: adapter timeout" in output
