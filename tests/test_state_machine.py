"""Tests for capsaicin state machine module (T08)."""

from __future__ import annotations

import pytest

from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.state_machine import (
    LEGAL_TRANSITIONS,
    DependenciesNotSatisfiedError,
    IllegalTransitionError,
    transition_is_legal,
    transition_ticket,
)
from capsaicin.ticket_add import _get_project_id, add_ticket_inline


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


def _set_status(conn, ticket_id, status, gate_reason=None, blocked_reason=None):
    """Directly set a ticket's status for test setup."""
    conn.execute(
        "UPDATE tickets SET status = ?, gate_reason = ?, blocked_reason = ? WHERE id = ?",
        (status, gate_reason, blocked_reason, ticket_id),
    )
    conn.commit()


class TestTransitionIsLegal:
    """Test the transition_is_legal function against state-machine.md rules."""

    def test_all_legal_transitions_accepted(self):
        """Every entry in LEGAL_TRANSITIONS should be accepted."""
        for (from_s, to_s), actors in LEGAL_TRANSITIONS.items():
            for actor in actors:
                assert transition_is_legal(from_s, to_s, actor), (
                    f"{from_s} -> {to_s} by {actor} should be legal"
                )

    def test_ready_to_implementing(self):
        assert transition_is_legal("ready", "implementing", "system")
        assert not transition_is_legal("ready", "implementing", "human")

    def test_implementing_to_in_review(self):
        assert transition_is_legal("implementing", "in-review", "system")

    def test_implementing_to_human_gate(self):
        assert transition_is_legal("implementing", "human-gate", "system")

    def test_implementing_to_blocked(self):
        assert transition_is_legal("implementing", "blocked", "system")

    def test_in_review_to_revise(self):
        assert transition_is_legal("in-review", "revise", "system")

    def test_in_review_to_human_gate(self):
        assert transition_is_legal("in-review", "human-gate", "system")

    def test_in_review_to_blocked(self):
        assert transition_is_legal("in-review", "blocked", "system")

    def test_revise_to_implementing(self):
        assert transition_is_legal("revise", "implementing", "system")

    def test_revise_to_human_gate(self):
        assert transition_is_legal("revise", "human-gate", "system")

    def test_human_gate_to_pr_ready(self):
        assert transition_is_legal("human-gate", "pr-ready", "human")
        assert not transition_is_legal("human-gate", "pr-ready", "system")

    def test_human_gate_to_revise(self):
        assert transition_is_legal("human-gate", "revise", "human")

    def test_human_gate_to_blocked(self):
        assert transition_is_legal("human-gate", "blocked", "human")

    def test_pr_ready_to_done(self):
        assert transition_is_legal("pr-ready", "done", "system")
        assert transition_is_legal("pr-ready", "done", "human")

    def test_blocked_to_ready(self):
        assert transition_is_legal("blocked", "ready", "human")
        assert not transition_is_legal("blocked", "ready", "system")

    def test_blocked_to_done(self):
        assert transition_is_legal("blocked", "done", "human")

    # Illegal transitions
    def test_revise_to_pr_ready_illegal(self):
        """revise -> pr-ready is explicitly illegal per state-machine.md."""
        assert not transition_is_legal("revise", "pr-ready", "system")
        assert not transition_is_legal("revise", "pr-ready", "human")

    def test_ready_to_pr_ready_illegal(self):
        """Cannot skip to pr-ready without human-gate."""
        assert not transition_is_legal("ready", "pr-ready", "system")
        assert not transition_is_legal("ready", "pr-ready", "human")

    def test_implementing_to_pr_ready_illegal(self):
        """Cannot skip to pr-ready without human-gate."""
        assert not transition_is_legal("implementing", "pr-ready", "system")

    def test_in_review_to_pr_ready_illegal(self):
        """Cannot skip to pr-ready without human-gate."""
        assert not transition_is_legal("in-review", "pr-ready", "system")

    def test_ready_to_done_illegal(self):
        """done only reachable from pr-ready or blocked."""
        assert not transition_is_legal("ready", "done", "system")

    def test_implementing_to_done_illegal(self):
        assert not transition_is_legal("implementing", "done", "system")

    def test_revise_to_done_illegal(self):
        assert not transition_is_legal("revise", "done", "system")

    def test_unknown_status_illegal(self):
        assert not transition_is_legal("nonexistent", "ready", "system")
        assert not transition_is_legal("ready", "nonexistent", "system")

    def test_wrong_actor_rejected(self):
        """System cannot approve through human-gate."""
        assert not transition_is_legal("human-gate", "pr-ready", "system")


class TestTransitionTicket:
    """Test the transition_ticket function with database integration."""

    def test_basic_transition(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        transition_ticket(conn, t, "implementing", "system", log_path=log)
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "implementing"

    def test_state_transition_row_created(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        transition_ticket(conn, t, "implementing", "system", reason="auto-select")
        row = conn.execute(
            "SELECT from_status, to_status, triggered_by, reason "
            "FROM state_transitions WHERE ticket_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (t,),
        ).fetchone()
        assert row[0] == "ready"
        assert row[1] == "implementing"
        assert row[2] == "system"
        assert row[3] == "auto-select"

    def test_status_changed_at_updated(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        old = conn.execute(
            "SELECT status_changed_at FROM tickets WHERE id = ?", (t,)
        ).fetchone()[0]
        transition_ticket(conn, t, "implementing", "system")
        new = conn.execute(
            "SELECT status_changed_at FROM tickets WHERE id = ?", (t,)
        ).fetchone()[0]
        assert new >= old

    def test_illegal_transition_raises(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        with pytest.raises(IllegalTransitionError, match="not allowed"):
            transition_ticket(conn, t, "pr-ready", "system")

    def test_nonexistent_ticket_raises(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            transition_ticket(conn, "FAKE", "implementing", "system")

    def test_gate_reason_set_on_human_gate(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "implementing")
        transition_ticket(
            conn, t, "human-gate", "system", gate_reason="empty_implementation"
        )
        row = conn.execute(
            "SELECT gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "empty_implementation"

    def test_gate_reason_cleared_on_leave(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "human-gate", gate_reason="review_passed")
        transition_ticket(conn, t, "pr-ready", "human")
        row = conn.execute(
            "SELECT gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] is None

    def test_blocked_reason_set_on_blocked(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "implementing")
        transition_ticket(
            conn, t, "blocked", "system", blocked_reason="implementation_failure"
        )
        row = conn.execute(
            "SELECT blocked_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "implementation_failure"

    def test_blocked_reason_cleared_on_unblock(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "blocked", blocked_reason="implementation_failure")
        transition_ticket(conn, t, "ready", "human")
        row = conn.execute(
            "SELECT blocked_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] is None

    def test_activity_log_entry(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        transition_ticket(conn, t, "implementing", "system", log_path=log)
        content = log.read_text()
        assert "STATE_TRANSITION" in content

    def test_full_happy_path(self, project):
        """Walk a ticket through ready -> implementing -> in-review ->
        human-gate -> pr-ready."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)

        transition_ticket(conn, t, "implementing", "system")
        transition_ticket(conn, t, "in-review", "system")
        transition_ticket(conn, t, "human-gate", "system", gate_reason="review_passed")
        transition_ticket(conn, t, "pr-ready", "human")

        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "pr-ready"

        # Verify all transitions recorded
        count = conn.execute(
            "SELECT COUNT(*) FROM state_transitions WHERE ticket_id = ?", (t,)
        ).fetchone()[0]
        # null->ready (from ticket add) + 4 transitions = 5
        assert count == 5

    def test_revise_loop(self, project):
        """Walk through a revise cycle: implementing -> in-review -> revise ->
        implementing -> in-review -> human-gate."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)

        transition_ticket(conn, t, "implementing", "system")
        transition_ticket(conn, t, "in-review", "system")
        transition_ticket(conn, t, "revise", "system")
        transition_ticket(conn, t, "implementing", "system")
        transition_ticket(conn, t, "in-review", "system")
        transition_ticket(conn, t, "human-gate", "system", gate_reason="review_passed")

        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "human-gate"


class TestDependencyGuard:
    """Test the dependency satisfaction guard for ready -> implementing."""

    def test_no_deps_allowed(self, project):
        """A ticket with no dependencies can transition freely."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        transition_ticket(conn, t, "implementing", "system")
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "implementing"

    def test_all_deps_done_allowed(self, project):
        """Transition allowed when all dependencies are done."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        dep = _add(conn, project_id, log, "Dep")
        t = _add(conn, project_id, log, "Main")

        # Add dependency
        conn.execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (t, dep),
        )
        conn.commit()

        # Mark dep as done
        _set_status(conn, dep, "done")

        transition_ticket(conn, t, "implementing", "system")
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "implementing"

    def test_unmet_deps_rejected(self, project):
        """Transition rejected when dependencies are not done."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        dep = _add(conn, project_id, log, "Dep")
        t = _add(conn, project_id, log, "Main")

        conn.execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (t, dep),
        )
        conn.commit()

        # dep is still in 'ready' status
        with pytest.raises(DependenciesNotSatisfiedError, match="unsatisfied"):
            transition_ticket(conn, t, "implementing", "system")

        # Ticket should remain in ready
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "ready"

    def test_partial_deps_rejected(self, project):
        """Transition rejected when only some dependencies are done."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        dep1 = _add(conn, project_id, log, "Dep1")
        dep2 = _add(conn, project_id, log, "Dep2")
        t = _add(conn, project_id, log, "Main")

        conn.execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (t, dep1),
        )
        conn.execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (t, dep2),
        )
        conn.commit()

        # Only mark one dep as done
        _set_status(conn, dep1, "done")

        with pytest.raises(DependenciesNotSatisfiedError):
            transition_ticket(conn, t, "implementing", "system")

    def test_dep_guard_only_on_ready_to_implementing(self, project):
        """Dependency guard should not apply to other transitions."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "implementing")

        # This should succeed even without checking deps
        transition_ticket(conn, t, "in-review", "system")
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "in-review"


class TestGuardConditions:
    """Test guard conditions from state-machine.md."""

    def test_implementing_to_human_gate_empty_impl(self, project):
        """implementing -> human-gate should set gate_reason='empty_implementation'."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "implementing")
        transition_ticket(
            conn, t, "human-gate", "system", gate_reason="empty_implementation"
        )
        row = conn.execute(
            "SELECT gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "empty_implementation"

    def test_in_review_to_human_gate_review_passed(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "in-review")
        transition_ticket(conn, t, "human-gate", "system", gate_reason="review_passed")
        row = conn.execute(
            "SELECT gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "review_passed"

    def test_in_review_to_human_gate_reviewer_escalated(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "in-review")
        transition_ticket(
            conn, t, "human-gate", "system", gate_reason="reviewer_escalated"
        )
        row = conn.execute(
            "SELECT gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "reviewer_escalated"

    def test_in_review_to_human_gate_low_confidence(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "in-review")
        transition_ticket(
            conn, t, "human-gate", "system", gate_reason="low_confidence_pass"
        )
        row = conn.execute(
            "SELECT gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "low_confidence_pass"

    def test_in_review_to_human_gate_cycle_limit(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "in-review")
        transition_ticket(conn, t, "human-gate", "system", gate_reason="cycle_limit")
        row = conn.execute(
            "SELECT gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "cycle_limit"

    def test_revise_to_human_gate_cycle_limit(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "revise")
        transition_ticket(conn, t, "human-gate", "system", gate_reason="cycle_limit")
        row = conn.execute(
            "SELECT gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "cycle_limit"

    def test_implementing_to_blocked_impl_failure(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "implementing")
        transition_ticket(
            conn,
            t,
            "blocked",
            "system",
            blocked_reason="implementation_failure",
        )
        row = conn.execute(
            "SELECT blocked_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "implementation_failure"

    def test_in_review_to_blocked_contract_violation(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "in-review")
        transition_ticket(
            conn,
            t,
            "blocked",
            "system",
            blocked_reason="reviewer_contract_violation",
        )
        row = conn.execute(
            "SELECT blocked_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "reviewer_contract_violation"

    def test_human_gate_to_blocked_defer(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "human-gate", gate_reason="review_passed")
        transition_ticket(
            conn, t, "blocked", "human", blocked_reason="deferred by human"
        )
        row = conn.execute(
            "SELECT blocked_reason, gate_reason FROM tickets WHERE id = ?", (t,)
        ).fetchone()
        assert row[0] == "deferred by human"
        assert row[1] is None  # gate_reason cleared

    def test_human_gate_without_gate_reason_rejected(self, project):
        """Transitioning to human-gate without gate_reason must fail."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "implementing")
        with pytest.raises(ValueError, match="gate_reason is required"):
            transition_ticket(conn, t, "human-gate", "system")
        # Ticket should remain unchanged
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "implementing"

    def test_blocked_without_blocked_reason_rejected(self, project):
        """Transitioning to blocked without blocked_reason must fail."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        _set_status(conn, t, "implementing")
        with pytest.raises(ValueError, match="blocked_reason is required"):
            transition_ticket(conn, t, "blocked", "system")
        # Ticket should remain unchanged
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (t,)).fetchone()
        assert row[0] == "implementing"
