"""Tests for app.commands — structured command services."""

from __future__ import annotations

import pytest

from capsaicin.app.commands import CommandResult
from capsaicin.app.commands.approve_ticket import approve
from capsaicin.app.commands.defer_ticket import defer
from capsaicin.app.commands.revise_ticket import revise
from capsaicin.app.commands.unblock_ticket import unblock
from capsaicin.app.context import AppContext
from capsaicin.errors import InvalidStatusError, NoEligibleTicketError
from tests.conftest import add_ticket, get_ticket, run_impl_to_in_review


# ---------------------------------------------------------------------------
# AppContext
# ---------------------------------------------------------------------------


class TestAppContext:
    def test_from_project_context(self, project_env):
        from capsaicin.project_context import resolve_context

        pctx = resolve_context(str(project_env["repo"]))
        with pctx:
            app = AppContext.from_project_context(pctx)
            assert app.project_id == project_env["project_id"]
            assert app.conn is pctx.conn
            assert app.config is pctx.config

    def test_refresh_config(self, project_env):
        from capsaicin.project_context import resolve_context

        pctx = resolve_context(str(project_env["repo"]))
        with pctx:
            app = AppContext.from_project_context(pctx)
            # Should not raise
            app.refresh_config()


# ---------------------------------------------------------------------------
# CommandResult
# ---------------------------------------------------------------------------


class TestCommandResult:
    def test_fields(self):
        r = CommandResult(
            ticket_id="T1",
            final_status="pr-ready",
            detail="approved",
            gate_reason=None,
            blocked_reason=None,
        )
        assert r.ticket_id == "T1"
        assert r.final_status == "pr-ready"
        assert r.detail == "approved"

    def test_defaults(self):
        r = CommandResult(ticket_id="T1", final_status="revise")
        assert r.detail is None
        assert r.gate_reason is None
        assert r.blocked_reason is None


# ---------------------------------------------------------------------------
# Revise command
# ---------------------------------------------------------------------------


class TestReviseCommand:
    def test_revise_returns_command_result(self, project_env):
        """Revise command returns a CommandResult with 'revise' status."""
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])

        # Get ticket to human-gate via implementation + review pass mock
        _move_to_human_gate(env, tid)

        result = revise(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket_id=tid,
            add_findings=["needs more tests"],
            log_path=env["log_path"],
        )

        assert isinstance(result, CommandResult)
        assert result.ticket_id == tid
        assert result.final_status == "revise"
        assert "1" in result.detail  # "Added 1 finding(s)"

    def test_revise_no_findings(self, project_env):
        env = project_env
        tid = add_ticket(env)
        _move_to_human_gate(env, tid)

        result = revise(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert result.final_status == "revise"
        assert result.detail is None

    def test_revise_wrong_status(self, project_env):
        env = project_env
        tid = add_ticket(env)

        with pytest.raises(InvalidStatusError):
            revise(
                conn=env["conn"],
                project_id=env["project_id"],
                ticket_id=tid,
                log_path=env["log_path"],
            )


# ---------------------------------------------------------------------------
# Defer command
# ---------------------------------------------------------------------------


class TestDeferCommand:
    def test_defer_returns_blocked(self, project_env):
        env = project_env
        tid = add_ticket(env)
        _move_to_human_gate(env, tid)

        result = defer(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket_id=tid,
            rationale="waiting on design",
            log_path=env["log_path"],
        )

        assert isinstance(result, CommandResult)
        assert result.ticket_id == tid
        assert result.final_status == "blocked"

    def test_defer_abandon_returns_done(self, project_env):
        env = project_env
        tid = add_ticket(env)
        _move_to_human_gate(env, tid)

        result = defer(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket_id=tid,
            rationale="out of scope",
            abandon=True,
            log_path=env["log_path"],
        )

        assert result.final_status == "done"


# ---------------------------------------------------------------------------
# Unblock command
# ---------------------------------------------------------------------------


class TestUnblockCommand:
    def test_unblock_returns_ready(self, project_env):
        env = project_env
        tid = add_ticket(env)
        _move_to_human_gate(env, tid)

        # Defer to blocked first
        defer(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket_id=tid,
            rationale="temp",
            log_path=env["log_path"],
        )

        result = unblock(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert isinstance(result, CommandResult)
        assert result.ticket_id == tid
        assert result.final_status == "ready"


# ---------------------------------------------------------------------------
# Approve command
# ---------------------------------------------------------------------------


class TestApproveCommand:
    def test_approve_returns_pr_ready(self, project_env):
        env = project_env
        tid = add_ticket(env)
        _move_to_human_gate(env, tid)

        result = approve(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            ticket_id=tid,
            force=True,  # skip workspace check in test
            log_path=env["log_path"],
        )

        assert isinstance(result, CommandResult)
        assert result.ticket_id == tid
        assert result.final_status == "pr-ready"

    def test_approve_auto_select(self, project_env):
        env = project_env
        tid = add_ticket(env)
        _move_to_human_gate(env, tid)

        result = approve(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            force=True,
            log_path=env["log_path"],
        )

        assert result.ticket_id == tid


# ---------------------------------------------------------------------------
# Loop command
# ---------------------------------------------------------------------------


class TestLoopCommand:
    def test_loop_resolves_ticket_id_when_omitted(self, project_env, monkeypatch):
        """When no ticket_id is passed, the result still carries the resolved ID."""
        env = project_env
        tid = add_ticket(env, title="Auto Loop")

        captured = {}

        def fake_run_loop(
            conn,
            project_id,
            config,
            impl_adapter,
            review_adapter,
            ticket_id=None,
            max_cycles=None,
            log_path=None,
            epic_id=None,
        ):
            captured["ticket_id"] = ticket_id
            return ("human-gate", "stopped")

        # Patch at source modules — the command uses lazy imports
        monkeypatch.setattr("capsaicin.loop.run_loop", fake_run_loop)
        monkeypatch.setattr(
            "capsaicin.adapters.claude_code.ClaudeCodeAdapter",
            lambda command: None,
        )

        from capsaicin.app.commands.loop import loop

        result = loop(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            log_path=env["log_path"],
        )

        assert isinstance(result, CommandResult)
        assert result.ticket_id == tid
        assert captured["ticket_id"] == tid

    def test_loop_preserves_explicit_ticket_id(self, project_env, monkeypatch):
        """When ticket_id is passed explicitly, the result carries it."""
        env = project_env
        tid = add_ticket(env, title="Explicit Loop")

        def fake_run_loop(
            conn,
            project_id,
            config,
            impl_adapter,
            review_adapter,
            ticket_id=None,
            max_cycles=None,
            log_path=None,
            epic_id=None,
        ):
            return ("human-gate", "stopped")

        monkeypatch.setattr("capsaicin.loop.run_loop", fake_run_loop)
        monkeypatch.setattr(
            "capsaicin.adapters.claude_code.ClaudeCodeAdapter",
            lambda command: None,
        )

        from capsaicin.app.commands.loop import loop

        result = loop(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert result.ticket_id == tid

    def test_loop_populates_gate_reason(self, project_env, monkeypatch):
        """gate_reason is read from the ticket after the loop ends."""
        env = project_env
        tid = add_ticket(env)

        def fake_run_loop(
            conn,
            project_id,
            config,
            impl_adapter,
            review_adapter,
            ticket_id=None,
            max_cycles=None,
            log_path=None,
            epic_id=None,
        ):
            # Simulate the loop moving the ticket to human-gate
            conn.execute(
                "UPDATE tickets SET status = 'human-gate', "
                "gate_reason = 'review_passed' WHERE id = ?",
                (ticket_id,),
            )
            conn.commit()
            return ("human-gate", "stopped")

        monkeypatch.setattr("capsaicin.loop.run_loop", fake_run_loop)
        monkeypatch.setattr(
            "capsaicin.adapters.claude_code.ClaudeCodeAdapter",
            lambda command: None,
        )

        from capsaicin.app.commands.loop import loop

        result = loop(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert result.gate_reason == "review_passed"


# ---------------------------------------------------------------------------
# Resume command
# ---------------------------------------------------------------------------


class TestResumeCommand:
    def test_resume_captures_ticket_id_before_pipeline(self, project_env, monkeypatch):
        """ticket_id is captured from orchestrator state before resume clears it."""
        env = project_env
        tid = add_ticket(env)

        # Set up orchestrator with an active ticket
        env["conn"].execute(
            "UPDATE orchestrator_state SET active_ticket_id = ?, status = 'running' "
            "WHERE project_id = ?",
            (tid, env["project_id"]),
        )
        env["conn"].commit()

        def fake_resume_pipeline(
            conn, project_id, config, impl_adapter, review_adapter, log_path=None
        ):
            # Simulate the pipeline clearing orchestrator state
            from capsaicin.orchestrator import set_idle

            set_idle(conn, project_id)
            return ("run", f"Ticket {tid} -> in-review")

        monkeypatch.setattr(
            "capsaicin.resume.resume_pipeline",
            fake_resume_pipeline,
        )
        monkeypatch.setattr(
            "capsaicin.adapters.claude_code.ClaudeCodeAdapter",
            lambda command: None,
        )

        from capsaicin.app.commands.resume import resume

        result = resume(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            log_path=env["log_path"],
        )

        assert isinstance(result, CommandResult)
        assert result.ticket_id == tid

    def test_resume_empty_ticket_when_idle(self, project_env, monkeypatch):
        """When orchestrator is idle with no active ticket, ticket_id is empty."""
        env = project_env

        def fake_resume_pipeline(
            conn, project_id, config, impl_adapter, review_adapter, log_path=None
        ):
            return ("idle", "No eligible ticket found.")

        monkeypatch.setattr(
            "capsaicin.resume.resume_pipeline",
            fake_resume_pipeline,
        )
        monkeypatch.setattr(
            "capsaicin.adapters.claude_code.ClaudeCodeAdapter",
            lambda command: None,
        )

        from capsaicin.app.commands.resume import resume

        result = resume(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            log_path=env["log_path"],
        )

        assert result.ticket_id == ""
        assert result.final_status == "idle"

    def test_resume_awaiting_human_preserves_ticket(self, project_env, monkeypatch):
        """When orchestrator is awaiting_human, the active ticket is preserved."""
        env = project_env
        tid = add_ticket(env)
        _move_to_human_gate(env, tid)

        def fake_resume_pipeline(
            conn, project_id, config, impl_adapter, review_adapter, log_path=None
        ):
            return ("awaiting_human", "Awaiting human decision.")

        monkeypatch.setattr(
            "capsaicin.resume.resume_pipeline",
            fake_resume_pipeline,
        )
        monkeypatch.setattr(
            "capsaicin.adapters.claude_code.ClaudeCodeAdapter",
            lambda command: None,
        )

        from capsaicin.app.commands.resume import resume

        result = resume(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            log_path=env["log_path"],
        )

        assert result.ticket_id == tid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _move_to_human_gate(env, ticket_id):
    """Move a ticket to human-gate via state machine transitions."""
    from capsaicin.orchestrator import await_human
    from capsaicin.state_machine import transition_ticket

    transition_ticket(
        env["conn"],
        ticket_id,
        "implementing",
        "system",
        reason="test",
    )
    transition_ticket(
        env["conn"],
        ticket_id,
        "human-gate",
        "system",
        reason="test",
        gate_reason="review_passed",
    )
    # Set orchestrator to awaiting_human with active_ticket_id
    env["conn"].execute(
        "UPDATE orchestrator_state SET active_ticket_id = ?, status = 'awaiting_human' "
        "WHERE project_id = ?",
        (ticket_id, env["project_id"]),
    )
    env["conn"].commit()
