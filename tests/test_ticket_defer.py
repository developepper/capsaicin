"""Tests for the defer/abandon pipeline (T23)."""

from __future__ import annotations

import pytest

from capsaicin.errors import (
    InvalidStatusError,
    NoEligibleTicketError,
    TicketNotFoundError,
)
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import ReviewResult, RunRequest, RunResult, ScopeReviewed
from capsaicin.orchestrator import get_state
from capsaicin.ticket_defer import defer_ticket, select_defer_ticket
from capsaicin.ticket_run import run_implementation_pipeline
from capsaicin.ticket_review import run_review_pipeline

from tests.adapters import DiffProducingAdapter
from tests.conftest import add_ticket, get_ticket, get_ticket_status


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


class MockReviewAdapter(BaseAdapter):
    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            structured_result=ReviewResult(
                verdict="pass",
                confidence="high",
                scope_reviewed=ScopeReviewed(files_examined=["impl.txt"]),
            ),
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _run_to_human_gate(env):
    tid = add_ticket(env, criteria=["criterion 1"])
    ticket = get_ticket(env["conn"], tid)

    impl_adapter = DiffProducingAdapter(env["repo"])
    run_implementation_pipeline(
        env["conn"],
        env["project_id"],
        ticket,
        env["config"],
        impl_adapter,
        log_path=env["log_path"],
    )

    ticket = get_ticket(env["conn"], tid)
    review_adapter = MockReviewAdapter()
    run_review_pipeline(
        env["conn"],
        env["project_id"],
        ticket,
        env["config"],
        review_adapter,
        log_path=env["log_path"],
    )

    assert get_ticket_status(env["conn"], tid) == "human-gate"
    return tid


# ---------------------------------------------------------------------------
# select_defer_ticket
# ---------------------------------------------------------------------------


class TestSelectDeferTicket:
    def test_auto_select(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = select_defer_ticket(env["conn"])
        assert ticket["id"] == tid

    def test_explicit_id(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = select_defer_ticket(env["conn"], tid)
        assert ticket["id"] == tid

    def test_wrong_status(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        with pytest.raises(InvalidStatusError, match="expected 'human-gate'"):
            select_defer_ticket(env["conn"], tid)

    def test_not_found(self, project_env):
        with pytest.raises(TicketNotFoundError, match="not found"):
            select_defer_ticket(project_env["conn"], "nonexistent")

    def test_no_human_gate_tickets(self, project_env):
        with pytest.raises(NoEligibleTicketError, match="No ticket found"):
            select_defer_ticket(project_env["conn"])


# ---------------------------------------------------------------------------
# Defer (no abandon) -> blocked
# ---------------------------------------------------------------------------


class TestDefer:
    def test_transitions_to_blocked(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        final = defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        assert final == "blocked"
        assert get_ticket_status(env["conn"], tid) == "blocked"

    def test_blocked_reason_set(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            rationale="Need more info",
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["blocked_reason"] == "Need more info"

    def test_default_blocked_reason(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["blocked_reason"] == "Deferred by human."

    def test_decision_recorded_as_defer(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            rationale="Later",
            log_path=env["log_path"],
        )

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        d = dict(rows[0])
        assert d["decision"] == "defer"
        assert d["rationale"] == "Later"

    def test_orchestrator_idle(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_state_transition_recorded(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        transitions = (
            env["conn"]
            .execute(
                "SELECT from_status, to_status FROM state_transitions "
                "WHERE ticket_id = ? ORDER BY id",
                (tid,),
            )
            .fetchall()
        )
        statuses = [(t["from_status"], t["to_status"]) for t in transitions]
        assert ("human-gate", "blocked") in statuses


# ---------------------------------------------------------------------------
# Abandon -> done
# ---------------------------------------------------------------------------


class TestAbandon:
    def test_transitions_to_done(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        final = defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            abandon=True,
            log_path=env["log_path"],
        )

        assert final == "done"
        assert get_ticket_status(env["conn"], tid) == "done"

    def test_decision_recorded_as_reject(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            abandon=True,
            rationale="Not needed",
            log_path=env["log_path"],
        )

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        d = dict(rows[0])
        assert d["decision"] == "reject"
        assert d["rationale"] == "Not needed"

    def test_orchestrator_idle(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            abandon=True,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_state_transitions_recorded(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            abandon=True,
            log_path=env["log_path"],
        )

        transitions = (
            env["conn"]
            .execute(
                "SELECT from_status, to_status FROM state_transitions "
                "WHERE ticket_id = ? ORDER BY id",
                (tid,),
            )
            .fetchall()
        )
        statuses = [(t["from_status"], t["to_status"]) for t in transitions]
        assert ("human-gate", "blocked") in statuses
        assert ("blocked", "done") in statuses


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_decision_logged(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "DECISION" in log_content
