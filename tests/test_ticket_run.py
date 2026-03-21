"""Tests for the implementation pipeline (T15)."""

from __future__ import annotations

import pytest

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import RunRequest, RunResult
from capsaicin.diff import get_run_diff
from capsaicin.orchestrator import get_state
from capsaicin.errors import (
    InvalidStatusError,
    NoEligibleTicketError,
    TicketNotFoundError,
)
from capsaicin.ticket_run import (
    run_implementation_pipeline,
    select_ticket,
)
from tests.adapters import DiffProducingAdapter
from tests.conftest import add_ticket, get_ticket, get_ticket_status


# ---------------------------------------------------------------------------
# Mock adapter
# ---------------------------------------------------------------------------


class MockAdapter(BaseAdapter):
    """Adapter that returns a pre-configured result."""

    def __init__(self, exit_status="success", duration=1.0):
        self.exit_status = exit_status
        self.duration = duration
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status=self.exit_status,
            duration_seconds=self.duration,
            raw_stdout="mock stdout",
            raw_stderr="",
            adapter_metadata={"mock": True},
        )


# ---------------------------------------------------------------------------
# select_ticket
# ---------------------------------------------------------------------------


class TestSelectTicket:
    def test_auto_select_ready(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = select_ticket(env["conn"])
        assert ticket["id"] == tid
        assert ticket["status"] == "ready"

    def test_auto_select_respects_created_at_ordering(self, project_env):
        env = project_env
        tid1 = add_ticket(env, title="First")
        add_ticket(env, title="Second")
        ticket = select_ticket(env["conn"])
        assert ticket["id"] == tid1

    def test_auto_select_skips_unmet_deps(self, project_env):
        env = project_env
        tid1 = add_ticket(env, title="Dep")
        tid2 = add_ticket(env, title="Blocked")
        # tid2 depends on tid1 (which is in 'ready', not 'done')
        env["conn"].execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (tid2, tid1),
        )
        env["conn"].commit()

        # tid1 should be selected (no deps), not tid2
        ticket = select_ticket(env["conn"])
        assert ticket["id"] == tid1

    def test_auto_select_picks_with_done_deps(self, project_env):
        env = project_env
        tid1 = add_ticket(env, title="Dep")
        tid2 = add_ticket(env, title="Dependent")
        env["conn"].execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            (tid2, tid1),
        )
        # Mark tid1 as done
        env["conn"].execute("UPDATE tickets SET status = 'done' WHERE id = ?", (tid1,))
        env["conn"].commit()

        ticket = select_ticket(env["conn"])
        assert ticket["id"] == tid2

    def test_explicit_ticket_id(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = select_ticket(env["conn"], tid)
        assert ticket["id"] == tid

    def test_explicit_ticket_wrong_status(self, project_env):
        env = project_env
        tid = add_ticket(env)
        env["conn"].execute(
            "UPDATE tickets SET status = 'blocked' WHERE id = ?", (tid,)
        )
        env["conn"].commit()
        with pytest.raises(InvalidStatusError, match="expected 'ready' or 'revise'"):
            select_ticket(env["conn"], tid)

    def test_explicit_ticket_not_found(self, project_env):
        with pytest.raises(TicketNotFoundError, match="not found"):
            select_ticket(project_env["conn"], "nonexistent")

    def test_no_eligible_tickets(self, project_env):
        with pytest.raises(NoEligibleTicketError, match="No eligible ticket"):
            select_ticket(project_env["conn"])

    def test_revise_status_accepted(self, project_env):
        env = project_env
        tid = add_ticket(env)
        env["conn"].execute("UPDATE tickets SET status = 'revise' WHERE id = ?", (tid,))
        env["conn"].commit()
        ticket = select_ticket(env["conn"], tid)
        assert ticket["status"] == "revise"


# ---------------------------------------------------------------------------
# run_implementation_pipeline — success + non-empty diff → in-review
# ---------------------------------------------------------------------------


class TestPipelineSuccessWithDiff:
    def test_transitions_to_in_review(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)

        adapter = DiffProducingAdapter(env["repo"])
        final = run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        assert final == "in-review"
        assert get_ticket_status(env["conn"], tid) == "in-review"

    def test_agent_run_created(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        runs = (
            env["conn"]
            .execute("SELECT * FROM agent_runs WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(runs) == 1
        run = dict(runs[0])
        assert run["role"] == "implementer"
        assert run["mode"] == "read-write"
        assert run["exit_status"] == "success"
        assert run["finished_at"] is not None

    def test_run_diffs_persisted(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        run = (
            env["conn"]
            .execute("SELECT id FROM agent_runs WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        diff = get_run_diff(env["conn"], run["id"])
        assert not diff.is_empty
        assert "impl.txt" in diff.files_changed

    def test_cycle_initialized(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        t = get_ticket(env["conn"], tid)
        assert t["current_cycle"] == 1

    def test_state_transitions_recorded(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
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
        assert ("ready", "implementing") in statuses
        assert ("implementing", "in-review") in statuses

    def test_orchestrator_idle_after_in_review(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"
        assert state["active_run_id"] is None
        assert state["active_ticket_id"] is None

    def test_adapter_receives_prompt(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Auth", desc="Add auth")
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        assert len(adapter.calls) == 1
        req = adapter.calls[0]
        assert req.role == "implementer"
        assert req.mode == "read-write"
        assert "Auth" in req.prompt
        assert "Add auth" in req.prompt


# ---------------------------------------------------------------------------
# success + empty diff → human-gate
# ---------------------------------------------------------------------------


class TestPipelineEmptyDiff:
    def test_transitions_to_human_gate(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        # MockAdapter doesn't modify files, so diff will be empty
        adapter = MockAdapter(exit_status="success")

        final = run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        assert final == "human-gate"
        assert get_ticket_status(env["conn"], tid) == "human-gate"

    def test_gate_reason_set(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = MockAdapter(exit_status="success")

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["gate_reason"] == "empty_implementation"

    def test_orchestrator_awaiting_human(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = MockAdapter(exit_status="success")

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"


# ---------------------------------------------------------------------------
# failure → retry → blocked
# ---------------------------------------------------------------------------


class TestPipelineFailure:
    def test_retry_increments_attempt(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        # max_impl_retries=2 in default config, so attempt 1 fails, attempt 2 fails → blocked
        adapter = MockAdapter(exit_status="failure")

        final = run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        assert final == "blocked"
        assert get_ticket_status(env["conn"], tid) == "blocked"

    def test_blocked_reason_set(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = MockAdapter(exit_status="failure")

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["blocked_reason"] == "implementation_failure"

    def test_multiple_run_records(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = MockAdapter(exit_status="failure")

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        runs = (
            env["conn"]
            .execute(
                "SELECT * FROM agent_runs WHERE ticket_id = ? ORDER BY started_at",
                (tid,),
            )
            .fetchall()
        )
        # Default max_impl_retries=2: attempt 1 → fail, attempt 2 → fail → blocked
        assert len(runs) == 2
        assert all(dict(r)["exit_status"] == "failure" for r in runs)

    def test_timeout_triggers_retry(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = MockAdapter(exit_status="timeout")

        final = run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        assert final == "blocked"

    def test_orchestrator_idle_after_blocked(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = MockAdapter(exit_status="failure")

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"


# ---------------------------------------------------------------------------
# cycle-limit shortcut from revise → human-gate
# ---------------------------------------------------------------------------


class TestCycleLimitShortcut:
    def test_revise_at_cycle_limit(self, project_env):
        env = project_env
        tid = add_ticket(env)
        # Manually put ticket into revise at cycle limit
        # Default max_cycles=3 in config
        env["conn"].execute(
            "UPDATE tickets SET status = 'revise', current_cycle = 3 WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()
        ticket = get_ticket(env["conn"], tid)

        adapter = MockAdapter()  # Should NOT be called
        final = run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        assert final == "human-gate"
        assert get_ticket_status(env["conn"], tid) == "human-gate"
        # Adapter should not have been invoked
        assert len(adapter.calls) == 0

    def test_gate_reason_cycle_limit(self, project_env):
        env = project_env
        tid = add_ticket(env)
        env["conn"].execute(
            "UPDATE tickets SET status = 'revise', current_cycle = 3 WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()
        ticket = get_ticket(env["conn"], tid)
        adapter = MockAdapter()

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["gate_reason"] == "cycle_limit"

    def test_orchestrator_awaiting_human(self, project_env):
        env = project_env
        tid = add_ticket(env)
        env["conn"].execute(
            "UPDATE tickets SET status = 'revise', current_cycle = 3 WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()
        ticket = get_ticket(env["conn"], tid)
        adapter = MockAdapter()

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"


# ---------------------------------------------------------------------------
# revise → implementing (under cycle limit)
# ---------------------------------------------------------------------------


class TestReviseUnderLimit:
    def test_revise_increments_cycle(self, project_env):
        env = project_env
        tid = add_ticket(env)
        # Put ticket in revise at cycle 1 (under limit of 3)
        env["conn"].execute(
            "UPDATE tickets SET status = 'revise', current_cycle = 1 WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        final = run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        assert final == "in-review"
        t = get_ticket(env["conn"], tid)
        assert t["current_cycle"] == 2


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_log_events_written(self, project_env):
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "RUN_START" in log_content
        assert "RUN_END" in log_content
        assert "STATE_TRANSITION" in log_content
