"""Tests for the ticket completion pipeline (T07)."""

from __future__ import annotations

import pytest

from capsaicin.errors import (
    InvalidStatusError,
    NoEligibleTicketError,
    TicketNotFoundError,
)
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import ReviewResult, RunRequest, RunResult, ScopeReviewed
from capsaicin.ticket_approve import approve_ticket
from capsaicin.ticket_complete import complete_ticket, select_complete_ticket
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


def _run_to_pr_ready(env):
    """Drive a ticket through impl -> review -> human-gate -> pr-ready."""
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

    ticket = get_ticket(env["conn"], tid)
    approve_ticket(
        env["conn"],
        env["project_id"],
        ticket,
        repo_path=env["repo"],
        force=True,
        log_path=env["log_path"],
    )

    assert get_ticket_status(env["conn"], tid) == "pr-ready"
    return tid


# ---------------------------------------------------------------------------
# select_complete_ticket
# ---------------------------------------------------------------------------


class TestSelectCompleteTicket:
    def test_auto_select(self, project_env):
        env = project_env
        tid = _run_to_pr_ready(env)
        ticket = select_complete_ticket(env["conn"])
        assert ticket["id"] == tid

    def test_explicit_id(self, project_env):
        env = project_env
        tid = _run_to_pr_ready(env)
        ticket = select_complete_ticket(env["conn"], tid)
        assert ticket["id"] == tid

    def test_wrong_status(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        with pytest.raises(InvalidStatusError, match="expected 'pr-ready'"):
            select_complete_ticket(env["conn"], tid)

    def test_not_found(self, project_env):
        with pytest.raises(TicketNotFoundError, match="not found"):
            select_complete_ticket(project_env["conn"], "nonexistent")

    def test_no_pr_ready_tickets(self, project_env):
        with pytest.raises(NoEligibleTicketError, match="No ticket found"):
            select_complete_ticket(project_env["conn"])


# ---------------------------------------------------------------------------
# complete_ticket -> done
# ---------------------------------------------------------------------------


class TestCompleteTicket:
    def test_transitions_to_done(self, project_env):
        env = project_env
        tid = _run_to_pr_ready(env)
        ticket = get_ticket(env["conn"], tid)

        final = complete_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        assert final == "done"
        assert get_ticket_status(env["conn"], tid) == "done"

    def test_decision_recorded_as_complete(self, project_env):
        env = project_env
        tid = _run_to_pr_ready(env)
        ticket = get_ticket(env["conn"], tid)

        complete_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            rationale="PR merged",
            log_path=env["log_path"],
        )

        rows = (
            env["conn"]
            .execute(
                "SELECT * FROM decisions WHERE ticket_id = ? AND decision = 'complete'",
                (tid,),
            )
            .fetchall()
        )
        assert len(rows) == 1
        d = dict(rows[0])
        assert d["decision"] == "complete"
        assert d["rationale"] == "PR merged"

    def test_default_reason(self, project_env):
        env = project_env
        tid = _run_to_pr_ready(env)
        ticket = get_ticket(env["conn"], tid)

        complete_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        transition = (
            env["conn"]
            .execute(
                "SELECT reason FROM state_transitions "
                "WHERE ticket_id = ? AND to_status = 'done' "
                "ORDER BY id DESC LIMIT 1",
                (tid,),
            )
            .fetchone()
        )
        assert transition["reason"] == "Completed by human."

    def test_state_transition_recorded(self, project_env):
        env = project_env
        tid = _run_to_pr_ready(env)
        ticket = get_ticket(env["conn"], tid)

        complete_ticket(
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
        assert ("pr-ready", "done") in statuses


# ---------------------------------------------------------------------------
# Dependency unblocking
# ---------------------------------------------------------------------------


class TestDependencyUnblocking:
    def test_downstream_becomes_runnable_after_completion(self, project_env):
        """After completing an upstream ticket, a dependent ticket's deps are satisfied."""
        from capsaicin.state_machine import _check_dependencies_satisfied
        from capsaicin.ticket_dep import add_dependency

        env = project_env

        # Create upstream and run it to pr-ready
        upstream_id = _run_to_pr_ready(env)

        # Create downstream that depends on upstream
        downstream_id = add_ticket(env, title="Downstream", criteria=["criterion"])
        add_dependency(env["conn"], downstream_id, upstream_id)

        # Before completion: deps not satisfied
        assert not _check_dependencies_satisfied(env["conn"], downstream_id)

        # Complete upstream
        ticket = get_ticket(env["conn"], upstream_id)
        complete_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        # After completion: deps satisfied
        assert _check_dependencies_satisfied(env["conn"], downstream_id)

    def test_pr_ready_does_not_satisfy_deps(self, project_env):
        """pr-ready alone does NOT satisfy dependencies — only done does."""
        from capsaicin.state_machine import _check_dependencies_satisfied
        from capsaicin.ticket_dep import add_dependency

        env = project_env

        upstream_id = _run_to_pr_ready(env)
        downstream_id = add_ticket(env, title="Downstream", criteria=["criterion"])
        add_dependency(env["conn"], downstream_id, upstream_id)

        # pr-ready does not satisfy deps
        assert not _check_dependencies_satisfied(env["conn"], downstream_id)


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_decision_logged(self, project_env):
        env = project_env
        tid = _run_to_pr_ready(env)
        ticket = get_ticket(env["conn"], tid)

        complete_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "DECISION" in log_content
        assert "complete" in log_content
