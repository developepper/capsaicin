"""Tests for loop auto-selection including revise tickets (T06).

Covers:
- ready-only queue: loop selects a ready ticket
- revise-only queue: loop selects a revise ticket
- mixed queue: loop prefers revise over ready
- revise ordering by status_changed_at
- revise tickets skip dependency checks
- cycle-limit shortcut still works for revise tickets
- ticket run auto-selection is unchanged (ready only)
- resume idle state is unchanged (ready only)
- explicit ticket_id bypasses loop selection
"""

from __future__ import annotations

import pytest

from capsaicin.errors import NoEligibleTicketError
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import RunRequest, RunResult
from capsaicin.loop import run_loop, select_ticket_for_loop
from capsaicin.resume import resume_pipeline
from capsaicin.ticket_run import select_ticket
from tests.conftest import add_ticket


# ---------------------------------------------------------------------------
# Mock adapter
# ---------------------------------------------------------------------------


class MockAdapter(BaseAdapter):
    """Adapter that returns success without modifying files (empty impl)."""

    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="{}",
            raw_stderr="",
            adapter_metadata={},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_status(conn, tid, status, **extra):
    sets = [f"status = '{status}'"]
    for k, v in extra.items():
        if isinstance(v, int):
            sets.append(f"{k} = {v}")
        elif v is None:
            sets.append(f"{k} = NULL")
        else:
            sets.append(f"{k} = '{v}'")
    conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", (tid,))
    conn.commit()


# ---------------------------------------------------------------------------
# select_ticket_for_loop
# ---------------------------------------------------------------------------


class TestSelectTicketForLoop:
    def test_ready_only(self, project_env):
        env = project_env
        tid = add_ticket(env, "Ready ticket")
        ticket = select_ticket_for_loop(env["conn"])
        assert ticket["id"] == tid
        assert ticket["status"] == "ready"

    def test_revise_only(self, project_env):
        """Loop should select a revise ticket even when no ready tickets exist."""
        env = project_env
        tid = add_ticket(env, "Revise ticket")
        _set_status(env["conn"], tid, "revise", current_cycle=1)
        ticket = select_ticket_for_loop(env["conn"])
        assert ticket["id"] == tid
        assert ticket["status"] == "revise"

    def test_prefers_revise_over_ready(self, project_env):
        """When both revise and ready exist, loop should prefer revise."""
        env = project_env
        add_ticket(env, "Ready ticket")
        revise_tid = add_ticket(env, "Revise ticket")
        _set_status(env["conn"], revise_tid, "revise", current_cycle=1)

        ticket = select_ticket_for_loop(env["conn"])
        assert ticket["id"] == revise_tid

    def test_revise_ordered_by_status_changed_at(self, project_env):
        """Multiple revise tickets: pick the one with earliest status_changed_at."""
        env = project_env
        tid1 = add_ticket(env, "First revise")
        tid2 = add_ticket(env, "Second revise")
        # Set tid2 with earlier status_changed_at
        _set_status(
            env["conn"],
            tid1,
            "revise",
            current_cycle=1,
            status_changed_at="2024-01-02T00:00:00Z",
        )
        _set_status(
            env["conn"],
            tid2,
            "revise",
            current_cycle=1,
            status_changed_at="2024-01-01T00:00:00Z",
        )

        ticket = select_ticket_for_loop(env["conn"])
        assert ticket["id"] == tid2

    def test_revise_skips_dependency_checks(self, project_env):
        """Revise tickets are in-flight — their deps should not be re-checked."""
        env = project_env
        dep_tid = add_ticket(env, "Dep ticket")
        revise_tid = add_ticket(env, "Revise ticket")
        # Add dependency that is NOT done
        env["conn"].execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (revise_tid, dep_tid),
        )
        env["conn"].commit()
        _set_status(env["conn"], revise_tid, "revise", current_cycle=1)

        # Should still select the revise ticket
        ticket = select_ticket_for_loop(env["conn"])
        assert ticket["id"] == revise_tid

    def test_explicit_ticket_id_delegates(self, project_env):
        env = project_env
        tid = add_ticket(env, "Explicit")
        ticket = select_ticket_for_loop(env["conn"], tid)
        assert ticket["id"] == tid

    def test_no_eligible_tickets(self, project_env):
        """No ready or revise tickets should raise ValueError."""
        with pytest.raises(NoEligibleTicketError, match="No eligible ticket"):
            select_ticket_for_loop(project_env["conn"])


# ---------------------------------------------------------------------------
# ticket run auto-selection unchanged
# ---------------------------------------------------------------------------


class TestTicketRunSelectionUnchanged:
    def test_ticket_run_does_not_select_revise(self, project_env):
        """select_ticket (used by ticket run) should NOT auto-select revise."""
        env = project_env
        tid = add_ticket(env, "Revise only")
        _set_status(env["conn"], tid, "revise", current_cycle=1)

        with pytest.raises(NoEligibleTicketError, match="No eligible ticket"):
            select_ticket(env["conn"])


# ---------------------------------------------------------------------------
# resume idle state unchanged (ready only)
# ---------------------------------------------------------------------------


class TestResumeIdleUnchanged:
    def test_resume_idle_does_not_select_revise(self, project_env):
        """resume in idle state should use ticket run semantics, not loop's
        revise-first selection.  When only a revise ticket exists, resume
        should report no eligible ticket rather than picking it up."""
        env = project_env
        tid = add_ticket(env, "Revise only")
        _set_status(env["conn"], tid, "revise", current_cycle=1)

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            impl_adapter=adapter,
            review_adapter=adapter,
            log_path=env["log_path"],
        )

        # resume in idle delegates to select_ticket which only picks ready
        assert action == "idle"
        assert "no eligible ticket" in detail.lower() or "no 'ready'" in detail.lower()
        # Adapter should NOT have been invoked
        assert len(adapter.calls) == 0


# ---------------------------------------------------------------------------
# Loop integration with revise
# ---------------------------------------------------------------------------


class TestLoopWithRevise:
    def test_loop_selects_revise_ticket(self, project_env):
        """Loop with no ticket_id should select a revise ticket."""
        env = project_env
        tid = add_ticket(env, "Revise ticket")
        _set_status(env["conn"], tid, "revise", current_cycle=1)

        adapter = MockAdapter()
        final_status, detail = run_loop(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            impl_adapter=adapter,
            review_adapter=MockAdapter(),
            log_path=env["log_path"],
        )

        # MockAdapter returns success with no file changes -> human-gate
        assert final_status == "human-gate"
        # Should have been invoked (not skipped)
        assert len(adapter.calls) >= 1

    def test_loop_revise_at_cycle_limit(self, project_env):
        """Revise ticket at cycle limit should shortcut to human-gate."""
        env = project_env
        tid = add_ticket(env, "At limit")
        _set_status(env["conn"], tid, "revise", current_cycle=3)

        adapter = MockAdapter()
        final_status, detail = run_loop(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            impl_adapter=adapter,
            review_adapter=MockAdapter(),
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        # Adapter should NOT be called (cycle-limit shortcut)
        assert len(adapter.calls) == 0

    def test_loop_mixed_queue_picks_revise(self, project_env):
        """Mixed queue: loop should pick revise, not ready."""
        env = project_env
        add_ticket(env, "Ready ticket")
        revise_tid = add_ticket(env, "Revise ticket")
        _set_status(env["conn"], revise_tid, "revise", current_cycle=1)

        adapter = MockAdapter()
        final_status, _ = run_loop(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            impl_adapter=adapter,
            review_adapter=MockAdapter(),
            log_path=env["log_path"],
        )

        # Should have worked on the revise ticket
        assert final_status == "human-gate"
        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (revise_tid,))
            .fetchone()
        )
        # Revise ticket should have been processed (now in human-gate)
        assert row["status"] == "human-gate"
