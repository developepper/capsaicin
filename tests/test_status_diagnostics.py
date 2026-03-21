"""Tests for status diagnostic visibility (T07).

Covers:
- permission-denied runs distinguishable in status output
- empty-implementation cases distinguishable in status output
- cost shown in last-run summary
- verbose mode shows agent text, denial summary, cost in run history
- the misleading exit=success + gate_reason=empty_implementation case
  is no longer opaque
"""

from __future__ import annotations

import json

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import RunRequest, RunResult
from capsaicin.ticket_run import run_implementation_pipeline
from capsaicin.ticket_status import build_ticket_detail
from tests.conftest import add_ticket, get_ticket


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


class PermissionDeniedAdapter(BaseAdapter):
    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        envelope = {
            "result": "Please grant write permission to proceed.",
            "is_error": False,
            "permission_denials": [
                {
                    "tool_name": "Edit",
                    "tool_use_id": "t1",
                    "tool_input": {"file_path": "/app/main.py"},
                },
                {
                    "tool_name": "Bash",
                    "tool_use_id": "t2",
                    "tool_input": {"command": "mkdir build"},
                },
            ],
        }
        return RunResult(
            run_id=request.run_id,
            exit_status="permission_denied",
            duration_seconds=200.0,
            result_text="Please grant write permission to proceed.",
            raw_stdout=json.dumps(envelope),
            raw_stderr="",
            adapter_metadata={
                "total_cost_usd": 0.85,
                "permission_denials": envelope["permission_denials"],
                "normalized_denials": [
                    {
                        "tool_name": "Edit",
                        "tool_use_id": "t1",
                        "file_path": "/app/main.py",
                    },
                    {
                        "tool_name": "Bash",
                        "tool_use_id": "t2",
                        "command": "mkdir build",
                    },
                ],
            },
        )


class EmptyImplAdapter(BaseAdapter):
    """Returns success without modifying files, with agent result text."""

    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        envelope = {
            "result": "I could not find any changes needed for this ticket.",
            "is_error": False,
        }
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=45.0,
            result_text="I could not find any changes needed for this ticket.",
            raw_stdout=json.dumps(envelope),
            raw_stderr="",
            adapter_metadata={"total_cost_usd": 0.034},
        )


# ---------------------------------------------------------------------------
# Permission-denied in status output
# ---------------------------------------------------------------------------


class TestStatusPermissionDenied:
    def test_default_shows_permission_denied(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Test", desc="Do it")
        ticket = get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        output = build_ticket_detail(env["conn"], tid)
        assert "permission_denied" in output
        assert "blocked by permission" in output.lower()
        assert "$0.8500" in output

    def test_verbose_shows_denial_details_and_agent_text(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Test", desc="Do it")
        ticket = get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        output = build_ticket_detail(env["conn"], tid, verbose=True)
        assert "Denials:" in output
        assert "Bash" in output
        assert "Edit" in output
        assert "Agent Text:" in output
        assert "grant write permission" in output.lower()


# ---------------------------------------------------------------------------
# Empty implementation in status output
# ---------------------------------------------------------------------------


class TestStatusEmptyImpl:
    def test_default_shows_no_changes(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Test", desc="Do it")
        ticket = get_ticket(env["conn"], tid)
        adapter = EmptyImplAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        output = build_ticket_detail(env["conn"], tid)
        assert "no changes" in output.lower()
        assert "$0.0340" in output

    def test_misleading_success_empty_impl_is_explained(self, project_env):
        """The old misleading case: exit=success + gate_reason=empty_implementation
        should now include diagnostic text, not just the raw exit status."""
        env = project_env
        tid = add_ticket(env, title="Test", desc="Do it")
        ticket = get_ticket(env["conn"], tid)
        adapter = EmptyImplAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        output = build_ticket_detail(env["conn"], tid)
        # Should show the diagnostic, not just "Exit Status: success"
        assert "no changes" in output.lower()
        # The exit status is still "success" in the run record
        assert "Exit Status: success" in output
        # But the gate reason context makes it clear what happened
        assert "empty_implementation" in output

    def test_verbose_shows_agent_text(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Test", desc="Do it")
        ticket = get_ticket(env["conn"], tid)
        adapter = EmptyImplAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        output = build_ticket_detail(env["conn"], tid, verbose=True)
        assert "Agent Text:" in output
        assert "could not find" in output.lower()


# ---------------------------------------------------------------------------
# Cost in status output
# ---------------------------------------------------------------------------


class TestStatusCost:
    def test_cost_shown_in_last_run(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Test", desc="Do it")
        ticket = get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        output = build_ticket_detail(env["conn"], tid)
        assert "Cost:" in output
        assert "$0.8500" in output

    def test_verbose_run_history_includes_cost(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Test", desc="Do it")
        ticket = get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        output = build_ticket_detail(env["conn"], tid, verbose=True)
        assert "cost=$0.8500" in output
