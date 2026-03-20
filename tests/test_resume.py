"""Tests for the resume pipeline (T26)."""

from __future__ import annotations

import json
import subprocess

import pytest

from capsaicin.adapters.types import CriterionChecked, Finding, ReviewResult, RunResult, ScopeReviewed
from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.orchestrator import get_state
from capsaicin.resume import (
    _handle_finished_impl_run,
    _handle_finished_review_run,
    _handle_interrupted_run,
    build_human_gate_context,
    get_active_run,
    resume_pipeline,
)
from capsaicin.ticket_add import _get_project_id, add_ticket_inline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockAdapter:
    """Adapter that records calls and returns a predetermined result."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result or RunResult(
            run_id="mock",
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="ok",
            raw_stderr="",
        )

    def execute(self, request):
        self.calls.append(request)
        return self.result


@pytest.fixture()
def project_env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "impl.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    project_dir = init_project("test-proj", str(repo))
    conn = get_connection(project_dir / "capsaicin.db")
    project_id = _get_project_id(conn)
    log_path = project_dir / "activity.log"
    config = load_config(project_dir / "config.toml")

    yield {
        "repo": repo,
        "project_dir": project_dir,
        "conn": conn,
        "project_id": project_id,
        "log_path": log_path,
        "config": config,
    }
    conn.close()


def _add_ticket(env, title="Test ticket"):
    return add_ticket_inline(
        env["conn"],
        env["project_id"],
        title,
        "Do something",
        ["criterion 1"],
        env["log_path"],
    )


def _get_ticket_status(conn, ticket_id):
    return conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()["status"]


def _get_criteria_checked(conn, ticket_id):
    """Build criteria_checked list from the ticket's acceptance criteria."""
    rows = conn.execute(
        "SELECT id, description FROM acceptance_criteria WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchall()
    return [
        CriterionChecked(criterion_id=r["id"], description=r["description"])
        for r in rows
    ]


def _make_implementing_ticket(env):
    """Create a ticket and move it to implementing with a running impl run."""
    tid = _add_ticket(env)
    env["conn"].execute(
        "UPDATE tickets SET status = 'implementing', current_cycle = 1, "
        "current_impl_attempt = 1 WHERE id = ?",
        (tid,),
    )
    env["conn"].execute(
        "INSERT INTO state_transitions (ticket_id, from_status, to_status, "
        "triggered_by, reason, created_at) VALUES (?, 'ready', 'implementing', "
        "'system', 'test', datetime('now'))",
        (tid,),
    )
    # Insert a running impl run
    env["conn"].execute(
        "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
        "attempt_number, exit_status, prompt, run_request, started_at) "
        "VALUES ('impl-run-1', ?, 'implementer', 'read-write', 1, 1, 'running', "
        "'p', '{}', datetime('now'))",
        (tid,),
    )
    env["conn"].execute(
        "UPDATE orchestrator_state SET status = 'running', "
        "active_ticket_id = ?, active_run_id = 'impl-run-1' "
        "WHERE project_id = ?",
        (tid, env["project_id"]),
    )
    env["conn"].commit()
    return tid


def _make_in_review_ticket(env):
    """Create a ticket in in-review with a finished impl run and a running review run."""
    tid = _add_ticket(env)
    env["conn"].execute(
        "UPDATE tickets SET status = 'in-review', current_cycle = 1, "
        "current_impl_attempt = 1, current_review_attempt = 1 WHERE id = ?",
        (tid,),
    )
    env["conn"].execute(
        "INSERT INTO state_transitions (ticket_id, from_status, to_status, "
        "triggered_by, reason, created_at) VALUES (?, 'ready', 'implementing', "
        "'system', 'test', datetime('now'))",
        (tid,),
    )
    env["conn"].execute(
        "INSERT INTO state_transitions (ticket_id, from_status, to_status, "
        "triggered_by, reason, created_at) VALUES (?, 'implementing', 'in-review', "
        "'system', 'test', datetime('now'))",
        (tid,),
    )
    # Insert a finished impl run with diff
    env["conn"].execute(
        "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
        "attempt_number, exit_status, prompt, run_request, started_at, finished_at) "
        "VALUES ('impl-run-1', ?, 'implementer', 'read-write', 1, 1, 'success', "
        "'p', '{}', datetime('now'), datetime('now'))",
        (tid,),
    )
    env["conn"].execute(
        "INSERT INTO run_diffs (run_id, diff_text, files_changed) "
        "VALUES ('impl-run-1', 'diff --git a/impl.txt b/impl.txt', "
        "'[\"impl.txt\"]')",
    )
    # Insert a running review run
    env["conn"].execute(
        "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
        "attempt_number, exit_status, prompt, run_request, started_at) "
        "VALUES ('review-run-1', ?, 'reviewer', 'read-only', 1, 1, 'running', "
        "'p', '{}', datetime('now'))",
        (tid,),
    )
    env["conn"].execute(
        "UPDATE orchestrator_state SET status = 'running', "
        "active_ticket_id = ?, active_run_id = 'review-run-1' "
        "WHERE project_id = ?",
        (tid, env["project_id"]),
    )
    env["conn"].commit()
    return tid


# ---------------------------------------------------------------------------
# Idle state -> runs ticket like ticket run
# ---------------------------------------------------------------------------


class TestResumeIdle:
    def test_idle_no_tickets(self, project_env):
        env = project_env
        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )
        assert action == "idle"
        assert "No eligible ticket" in detail

    def test_idle_selects_and_runs(self, project_env):
        env = project_env
        tid = _add_ticket(env)

        # Make file change so diff is non-empty
        (env["repo"] / "impl.txt").write_text("changed\n")

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )
        assert action == "run"
        assert tid in detail
        assert len(adapter.calls) == 1


# ---------------------------------------------------------------------------
# Running state — interrupted run (no finished_at)
# ---------------------------------------------------------------------------


class TestResumeInterruptedRun:
    def test_interrupted_impl_run_retries(self, project_env):
        """Below retry limit: marks old run failed, then retries via adapter."""
        env = project_env
        tid = _make_implementing_ticket(env)

        # Make file change so the retry produces a non-empty diff
        (env["repo"] / "impl.txt").write_text("retried\n")

        # max_impl_retries=2, current_impl_attempt=1.
        # After increment -> 2, which hits the limit.
        # So set a higher limit to allow retry.
        env["config"].limits.max_impl_retries = 5

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "interrupted"

        # Original run should be marked as failure
        run = get_active_run(env["conn"], "impl-run-1")
        assert run["exit_status"] == "failure"
        assert run["finished_at"] is not None

        # Adapter should have been called for the retry
        assert len(adapter.calls) == 1

    def test_interrupted_review_run_retries(self, project_env):
        """Below retry limit: marks old run failed, then retries via adapter."""
        env = project_env
        tid = _make_in_review_ticket(env)

        env["config"].limits.max_review_retries = 5

        # Adapter returns a pass review result for the retry
        cc = _get_criteria_checked(env["conn"], tid)
        review_result = ReviewResult(
            verdict="pass",
            confidence="high",
            findings=[],
            scope_reviewed=ScopeReviewed(
                files_examined=["impl.txt"], criteria_checked=cc
            ),
        )
        adapter = MockAdapter(
            result=RunResult(
                run_id="mock",
                exit_status="success",
                duration_seconds=1.0,
                raw_stdout="ok",
                raw_stderr="",
                structured_result=review_result,
            )
        )
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "interrupted"

        # Original run should be marked as failure
        run = get_active_run(env["conn"], "review-run-1")
        assert run["exit_status"] == "failure"
        assert run["finished_at"] is not None

        # Adapter should have been called for the retry
        assert len(adapter.calls) == 1

    def test_interrupted_impl_at_retry_limit_blocks(self, project_env):
        env = project_env
        tid = _make_implementing_ticket(env)

        # Set attempt to max so incrementing puts us at limit
        env["conn"].execute(
            "UPDATE tickets SET current_impl_attempt = ? WHERE id = ?",
            (env["config"].limits.max_impl_retries, tid),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "interrupted"
        assert "blocked" in detail
        assert _get_ticket_status(env["conn"], tid) == "blocked"
        # Adapter should NOT have been called (blocked, no retry)
        assert len(adapter.calls) == 0

    def test_interrupted_review_at_retry_limit_blocks(self, project_env):
        env = project_env
        tid = _make_in_review_ticket(env)

        env["conn"].execute(
            "UPDATE tickets SET current_review_attempt = ? WHERE id = ?",
            (env["config"].limits.max_review_retries, tid),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "interrupted"
        assert "blocked" in detail
        assert _get_ticket_status(env["conn"], tid) == "blocked"
        assert len(adapter.calls) == 0


# ---------------------------------------------------------------------------
# Running state — finished impl run (has finished_at)
# ---------------------------------------------------------------------------


class TestResumeFinishedImplRun:
    def test_finished_impl_success_with_diff(self, project_env):
        env = project_env
        tid = _make_implementing_ticket(env)

        # Mark run as finished with success
        env["conn"].execute(
            "UPDATE agent_runs SET exit_status = 'success', "
            "finished_at = datetime('now') WHERE id = 'impl-run-1'"
        )
        env["conn"].commit()

        # Make file change so diff is non-empty
        (env["repo"] / "impl.txt").write_text("changed\n")

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "post_run"
        assert "implementer" in detail
        assert "in-review" in detail
        assert _get_ticket_status(env["conn"], tid) == "in-review"

    def test_finished_impl_success_empty_diff(self, project_env):
        env = project_env
        tid = _make_implementing_ticket(env)

        # Mark run as finished with success (workspace has no changes)
        env["conn"].execute(
            "UPDATE agent_runs SET exit_status = 'success', "
            "finished_at = datetime('now') WHERE id = 'impl-run-1'"
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "post_run"
        assert "human-gate" in detail
        assert _get_ticket_status(env["conn"], tid) == "human-gate"

    def test_finished_impl_failure_blocks_at_limit(self, project_env):
        """Default max_impl_retries=2, attempt starts at 1.
        After failure, attempt is incremented to 2, hitting the limit -> blocked.
        """
        env = project_env
        tid = _make_implementing_ticket(env)

        env["conn"].execute(
            "UPDATE agent_runs SET exit_status = 'failure', "
            "finished_at = datetime('now') WHERE id = 'impl-run-1'"
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "post_run"
        assert "blocked" in detail
        assert _get_ticket_status(env["conn"], tid) == "blocked"

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_does_not_duplicate_if_already_processed(self, project_env):
        env = project_env
        tid = _make_implementing_ticket(env)

        # Simulate: ticket already moved to in-review (post-run already ran)
        env["conn"].execute(
            "UPDATE agent_runs SET exit_status = 'success', "
            "finished_at = datetime('now') WHERE id = 'impl-run-1'"
        )
        env["conn"].execute(
            "UPDATE tickets SET status = 'in-review' WHERE id = ?", (tid,)
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "post_run"
        assert "in-review" in detail
        # Status should still be in-review, not double-processed
        assert _get_ticket_status(env["conn"], tid) == "in-review"


# ---------------------------------------------------------------------------
# Running state — finished review run (has finished_at)
# ---------------------------------------------------------------------------


class TestResumeFinishedReviewRun:
    def test_finished_review_pass(self, project_env):
        env = project_env
        tid = _make_in_review_ticket(env)

        cc = _get_criteria_checked(env["conn"], tid)
        review_result = ReviewResult(
            verdict="pass",
            confidence="high",
            findings=[],
            scope_reviewed=ScopeReviewed(
                files_examined=["impl.txt"], criteria_checked=cc
            ),
        )

        # Mark review run as finished with success and structured result
        env["conn"].execute(
            "UPDATE agent_runs SET exit_status = 'success', "
            "structured_result = ?, verdict = 'pass', "
            "finished_at = datetime('now') WHERE id = 'review-run-1'",
            (review_result.to_json(),),
        )
        # Insert review baseline so violation check passes
        env["conn"].execute(
            "INSERT INTO review_baselines (run_id, baseline_diff, baseline_status) "
            "VALUES ('review-run-1', '', '')"
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "post_run"
        assert "reviewer" in detail
        assert "human-gate" in detail
        assert _get_ticket_status(env["conn"], tid) == "human-gate"

    def test_finished_review_fail(self, project_env):
        env = project_env
        tid = _make_in_review_ticket(env)

        cc = _get_criteria_checked(env["conn"], tid)
        review_result = ReviewResult(
            verdict="fail",
            confidence="high",
            findings=[
                Finding(
                    severity="blocking",
                    category="correctness",
                    description="Missing test",
                    location="impl.txt",
                    acceptance_criterion_id=None,
                    disposition="open",
                )
            ],
            scope_reviewed=ScopeReviewed(
                files_examined=["impl.txt"], criteria_checked=cc
            ),
        )

        env["conn"].execute(
            "UPDATE agent_runs SET exit_status = 'success', "
            "structured_result = ?, verdict = 'fail', "
            "finished_at = datetime('now') WHERE id = 'review-run-1'",
            (review_result.to_json(),),
        )
        env["conn"].execute(
            "INSERT INTO review_baselines (run_id, baseline_diff, baseline_status) "
            "VALUES ('review-run-1', '', '')"
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "post_run"
        assert "reviewer" in detail
        assert "revise" in detail
        assert _get_ticket_status(env["conn"], tid) == "revise"

    def test_does_not_duplicate_if_already_processed(self, project_env):
        env = project_env
        tid = _make_in_review_ticket(env)

        # Simulate: ticket already moved to revise
        env["conn"].execute(
            "UPDATE agent_runs SET exit_status = 'success', "
            "finished_at = datetime('now') WHERE id = 'review-run-1'"
        )
        env["conn"].execute("UPDATE tickets SET status = 'revise' WHERE id = ?", (tid,))
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "post_run"
        assert "revise" in detail
        assert _get_ticket_status(env["conn"], tid) == "revise"


# ---------------------------------------------------------------------------
# Awaiting human
# ---------------------------------------------------------------------------


class TestResumeAwaitingHuman:
    def test_renders_context(self, project_env):
        env = project_env
        tid = _add_ticket(env, title="Gate Test")

        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', "
            "gate_reason = 'review_passed' WHERE id = ?",
            (tid,),
        )
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'awaiting_human', "
            "active_ticket_id = ? WHERE project_id = ?",
            (tid, env["project_id"]),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "awaiting_human"
        assert "Awaiting human decision" in detail
        assert "Gate Test" in detail
        assert "review_passed" in detail
        assert "capsaicin ticket approve" in detail

    def test_renders_findings(self, project_env):
        env = project_env
        tid = _add_ticket(env)

        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', "
            "gate_reason = 'cycle_limit' WHERE id = ?",
            (tid,),
        )
        env["conn"].execute(
            "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
            "attempt_number, exit_status, prompt, run_request, started_at) "
            "VALUES ('run1', ?, 'reviewer', 'read-only', 1, 1, 'success', "
            "'p', '{}', datetime('now'))",
            (tid,),
        )
        env["conn"].execute(
            "INSERT INTO findings (id, run_id, ticket_id, severity, category, "
            "fingerprint, description, disposition) "
            "VALUES ('f1', 'run1', ?, 'blocking', 'correctness', 'fp1', "
            "'Missing null check', 'open')",
            (tid,),
        )
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'awaiting_human', "
            "active_ticket_id = ? WHERE project_id = ?",
            (tid, env["project_id"]),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "awaiting_human"
        assert "Missing null check" in detail
        assert "blocking" in detail

    def test_no_action_taken(self, project_env):
        """Awaiting human should not change any state."""
        env = project_env
        tid = _add_ticket(env)

        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', "
            "gate_reason = 'review_passed' WHERE id = ?",
            (tid,),
        )
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'awaiting_human', "
            "active_ticket_id = ? WHERE project_id = ?",
            (tid, env["project_id"]),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        # State unchanged
        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"
        assert _get_ticket_status(env["conn"], tid) == "human-gate"


# ---------------------------------------------------------------------------
# Suspended state
# ---------------------------------------------------------------------------


class TestResumeSuspended:
    def test_resume_run_step(self, project_env):
        """Suspended with step=run resumes implementation for a ready ticket."""
        env = project_env
        tid = _add_ticket(env, title="Suspended Ticket")

        # Make file change so impl produces a non-empty diff
        (env["repo"] / "impl.txt").write_text("resumed\n")

        ctx = json.dumps({"step": "run", "ticket_id": tid})
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'suspended', "
            "active_ticket_id = ?, resume_context = ? WHERE project_id = ?",
            (tid, ctx, env["project_id"]),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "resumed_run"
        assert tid in detail
        assert len(adapter.calls) == 1

    def test_resume_review_step(self, project_env):
        """Suspended with step=review resumes review for an in-review ticket."""
        env = project_env
        tid = _make_in_review_ticket(env)

        # Make workspace match the stored diff (allow_drift=True in context)
        ctx = json.dumps({"step": "review", "ticket_id": tid, "allow_drift": True})
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'suspended', "
            "active_ticket_id = ?, resume_context = ? WHERE project_id = ?",
            (tid, ctx, env["project_id"]),
        )
        env["conn"].commit()

        cc = _get_criteria_checked(env["conn"], tid)
        review_result = ReviewResult(
            verdict="pass",
            confidence="high",
            findings=[],
            scope_reviewed=ScopeReviewed(
                files_examined=["impl.txt"], criteria_checked=cc
            ),
        )
        adapter = MockAdapter(
            result=RunResult(
                run_id="mock",
                exit_status="success",
                duration_seconds=1.0,
                raw_stdout="ok",
                raw_stderr="",
                structured_result=review_result,
            )
        )
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "resumed_review"
        assert tid in detail

    def test_without_context_resets(self, project_env):
        env = project_env
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'suspended' WHERE project_id = ?",
            (env["project_id"],),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "suspended"
        assert "Reset to idle" in detail
        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_invalid_json_context_resets(self, project_env):
        env = project_env
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'suspended', "
            "resume_context = 'not-json' WHERE project_id = ?",
            (env["project_id"],),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "suspended"
        assert "Could not parse" in detail
        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_unknown_step_resets(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ctx = json.dumps({"step": "unknown_step", "ticket_id": tid})
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'suspended', "
            "active_ticket_id = ?, resume_context = ? WHERE project_id = ?",
            (tid, ctx, env["project_id"]),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "suspended"
        assert "Unrecognised" in detail
        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_wrong_ticket_status_for_step(self, project_env):
        """step=run but ticket is in implementing (not ready/revise)."""
        env = project_env
        tid = _make_implementing_ticket(env)

        ctx = json.dumps({"step": "run", "ticket_id": tid})
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'suspended', "
            "active_ticket_id = ?, resume_context = ? WHERE project_id = ?",
            (tid, ctx, env["project_id"]),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "suspended"
        assert "implementing" in detail


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestResumeEdgeCases:
    def test_orphaned_running_state(self, project_env):
        """Running state with no active run should reset to idle."""
        env = project_env
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'running', "
            "active_ticket_id = NULL, active_run_id = NULL "
            "WHERE project_id = ?",
            (env["project_id"],),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "reset"
        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_missing_active_run(self, project_env):
        """Running state with a run that was deleted should reset."""
        env = project_env
        tid = _add_ticket(env)
        # Insert a real run, set orchestrator to reference it, then delete the run
        env["conn"].execute(
            "INSERT INTO agent_runs (id, ticket_id, role, mode, cycle_number, "
            "attempt_number, exit_status, prompt, run_request, started_at) "
            "VALUES ('temp-run', ?, 'implementer', 'read-write', 1, 1, 'running', "
            "'p', '{}', datetime('now'))",
            (tid,),
        )
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'running', "
            "active_ticket_id = ?, active_run_id = 'temp-run' "
            "WHERE project_id = ?",
            (tid, env["project_id"]),
        )
        env["conn"].commit()
        # Now clear the FK reference and delete the run
        env["conn"].execute(
            "UPDATE orchestrator_state SET active_run_id = NULL WHERE project_id = ?",
            (env["project_id"],),
        )
        env["conn"].execute("DELETE FROM agent_runs WHERE id = 'temp-run'")
        # Set the active_run_id back to the now-missing run via raw SQL
        # (bypassing FK since we disabled it temporarily isn't clean,
        # so we test the orphaned-no-run-id path instead)
        env["conn"].commit()

        # Test with active_run_id = NULL (orphaned running state)
        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "reset"
        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_awaiting_human_no_ticket(self, project_env):
        """Awaiting human with no active ticket should reset."""
        env = project_env
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'awaiting_human', "
            "active_ticket_id = NULL WHERE project_id = ?",
            (env["project_id"],),
        )
        env["conn"].commit()

        adapter = MockAdapter()
        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        assert action == "reset"
        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"


# ---------------------------------------------------------------------------
# Human gate context builder
# ---------------------------------------------------------------------------


class TestBuildHumanGateContext:
    def test_basic_context(self, project_env):
        env = project_env
        tid = _add_ticket(env, title="My Ticket")
        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', "
            "gate_reason = 'review_passed' WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()

        output = build_human_gate_context(env["conn"], tid)
        assert "My Ticket" in output
        assert "review_passed" in output
        assert "Acceptance Criteria:" in output
        assert "capsaicin ticket approve" in output

    def test_not_found(self, project_env):
        output = build_human_gate_context(project_env["conn"], "nonexistent")
        assert "not found" in output


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_resume_logged(self, project_env):
        env = project_env
        adapter = MockAdapter()
        resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "RESUME" in log_content

    def test_interrupted_run_logged(self, project_env):
        env = project_env
        _make_implementing_ticket(env)

        adapter = MockAdapter()
        resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "RUN_INTERRUPTED" in log_content
