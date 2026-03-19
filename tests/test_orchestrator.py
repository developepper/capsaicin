"""Tests for capsaicin orchestrator state management (T09)."""

from __future__ import annotations

import pytest

from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.orchestrator import (
    await_human,
    check_cycle_limit,
    check_impl_retry_limit,
    check_review_retry_limit,
    finish_run,
    get_state,
    increment_cycle,
    increment_impl_attempt,
    increment_review_attempt,
    init_cycle,
    reset_counters,
    set_idle,
    start_run,
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


def _create_run(conn, ticket_id, run_id="run-001"):
    """Insert a minimal agent_runs row so FK constraints are satisfied."""
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'implementer', 'read-write', 1, 1, "
        "'running', 'test', '{}', datetime('now'))",
        (run_id, ticket_id),
    )
    conn.commit()
    return run_id


def _counters(conn, ticket_id):
    """Return (current_cycle, current_impl_attempt, current_review_attempt)."""
    row = conn.execute(
        "SELECT current_cycle, current_impl_attempt, current_review_attempt "
        "FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    return row[0], row[1], row[2]


class TestOrchestratorState:
    """Test orchestrator_state transitions."""

    def test_initial_state_is_idle(self, project):
        _, conn, project_id = project
        state = get_state(conn, project_id)
        assert state["status"] == "idle"
        assert state["active_ticket_id"] is None
        assert state["active_run_id"] is None

    def test_start_run(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        run_id = _create_run(conn, t)
        start_run(conn, project_id, t, run_id)
        state = get_state(conn, project_id)
        assert state["status"] == "running"
        assert state["active_ticket_id"] == t
        assert state["active_run_id"] == run_id

    def test_finish_run_clears_run_keeps_ticket(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        run_id = _create_run(conn, t)
        start_run(conn, project_id, t, run_id)
        finish_run(conn, project_id)
        state = get_state(conn, project_id)
        assert state["active_run_id"] is None
        assert state["active_ticket_id"] == t

    def test_await_human(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        run_id = _create_run(conn, t)
        start_run(conn, project_id, t, run_id)
        await_human(conn, project_id)
        state = get_state(conn, project_id)
        assert state["status"] == "awaiting_human"

    def test_set_idle_clears_everything(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        run_id = _create_run(conn, t)
        start_run(conn, project_id, t, run_id)
        set_idle(conn, project_id)
        state = get_state(conn, project_id)
        assert state["status"] == "idle"
        assert state["active_ticket_id"] is None
        assert state["active_run_id"] is None

    def test_full_lifecycle(self, project):
        """idle -> running -> awaiting_human -> idle."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        run_id = _create_run(conn, t)

        start_run(conn, project_id, t, run_id)
        assert get_state(conn, project_id)["status"] == "running"

        finish_run(conn, project_id)
        await_human(conn, project_id)
        assert get_state(conn, project_id)["status"] == "awaiting_human"

        set_idle(conn, project_id)
        assert get_state(conn, project_id)["status"] == "idle"

    def test_get_state_nonexistent_project(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="No orchestrator state"):
            get_state(conn, "nonexistent")

    def test_start_run_nonexistent_project(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        run_id = _create_run(conn, t)
        with pytest.raises(ValueError, match="not found"):
            start_run(conn, "nonexistent", t, run_id)

    def test_finish_run_nonexistent_project(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            finish_run(conn, "nonexistent")

    def test_await_human_nonexistent_project(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            await_human(conn, "nonexistent")

    def test_set_idle_nonexistent_project(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            set_idle(conn, "nonexistent")


class TestCycleCounters:
    """Test cycle/retry counter helpers."""

    def test_init_cycle(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        assert _counters(conn, t) == (1, 1, 1)

    def test_increment_cycle(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_cycle(conn, t)
        cycle, impl, review = _counters(conn, t)
        assert cycle == 2
        assert impl == 1  # reset on cycle increment
        assert review == 1  # unchanged

    def test_increment_impl_attempt(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_impl_attempt(conn, t)
        cycle, impl, review = _counters(conn, t)
        assert cycle == 1  # unchanged
        assert impl == 2
        assert review == 1  # unchanged

    def test_increment_review_attempt(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_review_attempt(conn, t)
        cycle, impl, review = _counters(conn, t)
        assert cycle == 1  # unchanged
        assert impl == 1  # unchanged
        assert review == 2

    def test_retry_counters_independent_of_cycle(self, project):
        """Incrementing impl/review attempts does not affect cycle counter."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_impl_attempt(conn, t)
        increment_impl_attempt(conn, t)
        increment_review_attempt(conn, t)
        cycle, impl, review = _counters(conn, t)
        assert cycle == 1
        assert impl == 3
        assert review == 2

    def test_cycle_increment_resets_impl_attempt(self, project):
        """Incrementing cycle resets impl_attempt but not review_attempt."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_impl_attempt(conn, t)
        increment_impl_attempt(conn, t)
        increment_review_attempt(conn, t)
        increment_cycle(conn, t)
        cycle, impl, review = _counters(conn, t)
        assert cycle == 2
        assert impl == 1  # reset
        assert review == 2  # preserved

    def test_reset_counters(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_cycle(conn, t)
        increment_cycle(conn, t)
        increment_impl_attempt(conn, t)
        increment_review_attempt(conn, t)
        reset_counters(conn, t)
        assert _counters(conn, t) == (0, 1, 1)

    def test_multiple_cycles(self, project):
        """Simulate 3 full cycles."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        assert _counters(conn, t) == (1, 1, 1)

        increment_cycle(conn, t)
        assert _counters(conn, t) == (2, 1, 1)

        increment_cycle(conn, t)
        assert _counters(conn, t) == (3, 1, 1)

    def test_init_cycle_nonexistent_ticket(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            init_cycle(conn, "FAKE")

    def test_increment_cycle_nonexistent_ticket(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            increment_cycle(conn, "FAKE")

    def test_increment_impl_attempt_nonexistent_ticket(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            increment_impl_attempt(conn, "FAKE")

    def test_increment_review_attempt_nonexistent_ticket(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            increment_review_attempt(conn, "FAKE")

    def test_reset_counters_nonexistent_ticket(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            reset_counters(conn, "FAKE")


class TestLimitChecks:
    """Test cycle/retry limit boundary checks."""

    def test_cycle_below_limit(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)  # cycle=1
        assert check_cycle_limit(conn, t, 3) is False

    def test_cycle_at_limit(self, project):
        """cycle=3, max=3 -> True (reached limit)."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_cycle(conn, t)
        increment_cycle(conn, t)  # cycle=3
        assert check_cycle_limit(conn, t, 3) is True

    def test_cycle_above_limit(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_cycle(conn, t)
        increment_cycle(conn, t)
        increment_cycle(conn, t)  # cycle=4
        assert check_cycle_limit(conn, t, 3) is True

    def test_impl_retry_below_limit(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)  # impl_attempt=1
        assert check_impl_retry_limit(conn, t, 2) is False

    def test_impl_retry_at_limit(self, project):
        """impl_attempt=2, max=2 -> True."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_impl_attempt(conn, t)  # impl_attempt=2
        assert check_impl_retry_limit(conn, t, 2) is True

    def test_review_retry_below_limit(self, project):
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)  # review_attempt=1
        assert check_review_retry_limit(conn, t, 2) is False

    def test_review_retry_at_limit(self, project):
        """review_attempt=2, max=2 -> True."""
        project_dir, conn, project_id = project
        log = project_dir / "activity.log"
        t = _add(conn, project_id, log)
        init_cycle(conn, t)
        increment_review_attempt(conn, t)  # review_attempt=2
        assert check_review_retry_limit(conn, t, 2) is True

    def test_limit_check_nonexistent_ticket(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            check_cycle_limit(conn, "FAKE", 3)

    def test_limit_check_impl_nonexistent_ticket(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            check_impl_retry_limit(conn, "FAKE", 2)

    def test_limit_check_review_nonexistent_ticket(self, project):
        _, conn, _ = project
        with pytest.raises(ValueError, match="not found"):
            check_review_retry_limit(conn, "FAKE", 2)
