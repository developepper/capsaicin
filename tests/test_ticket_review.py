"""Tests for the review pipeline (T20)."""

from __future__ import annotations

import json
import subprocess

import pytest

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import (
    Finding,
    ReviewResult,
    RunRequest,
    RunResult,
    ScopeReviewed,
    CriterionChecked,
)
from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.diff import capture_diff, persist_run_diff
from capsaicin.init import init_project
from capsaicin.orchestrator import get_state
from capsaicin.ticket_add import _get_project_id, add_ticket_inline
from capsaicin.ticket_run import run_implementation_pipeline, select_ticket
from capsaicin.ticket_review import (
    run_review_pipeline,
    select_review_ticket,
)


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


class DiffProducingAdapter(BaseAdapter):
    """Adapter that modifies a file in the repo before returning success."""

    def __init__(self, repo_path, filename="impl.txt", content="implemented\n"):
        self.repo_path = repo_path
        self.filename = filename
        self.content = content
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        (self.repo_path / self.filename).write_text(self.content)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            adapter_metadata={},
        )


class MockReviewAdapter(BaseAdapter):
    """Adapter that returns a pre-configured ReviewResult."""

    def __init__(
        self,
        verdict="pass",
        confidence="high",
        findings=None,
        scope_reviewed=None,
        exit_status="success",
    ):
        self.verdict = verdict
        self.confidence = confidence
        self.findings = findings or []
        self.scope_reviewed = scope_reviewed
        self.exit_status = exit_status
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)

        if self.exit_status != "success":
            return RunResult(
                run_id=request.run_id,
                exit_status=self.exit_status,
                duration_seconds=1.0,
                raw_stdout="",
                raw_stderr="error",
                adapter_metadata={},
            )

        # Build scope_reviewed with files_examined populated
        sr = self.scope_reviewed or ScopeReviewed(
            files_examined=["impl.txt"],
            tests_run=False,
            criteria_checked=[],
        )

        review_result = ReviewResult(
            verdict=self.verdict,
            confidence=self.confidence,
            findings=self.findings,
            scope_reviewed=sr,
        )

        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="review done",
            raw_stderr="",
            structured_result=review_result,
            adapter_metadata={"mock": True},
        )


class FileModifyingReviewAdapter(BaseAdapter):
    """Adapter that modifies tracked files (contract violation)."""

    def __init__(self, repo_path, verdict="pass", confidence="high"):
        self.repo_path = repo_path
        self.verdict = verdict
        self.confidence = confidence
        self.calls: list[RunRequest] = []
        self._call_count = 0

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        self._call_count += 1
        # Modify a tracked file — contract violation (unique content each call)
        (self.repo_path / "impl.txt").write_text(
            f"reviewer was here {self._call_count}\n"
        )

        review_result = ReviewResult(
            verdict=self.verdict,
            confidence=self.confidence,
            findings=[],
            scope_reviewed=ScopeReviewed(files_examined=["impl.txt"]),
        )

        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            structured_result=review_result,
            adapter_metadata={},
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_env(tmp_path):
    """Set up a project with a git repo, returning context dict."""
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


def _add_ticket(env, title="Test ticket", desc="Do something", criteria=None):
    return add_ticket_inline(
        env["conn"],
        env["project_id"],
        title,
        desc,
        criteria or [],
        env["log_path"],
    )


def _add_ticket_with_criteria(env, title="Test", desc="Do it", criteria=None):
    return add_ticket_inline(
        env["conn"],
        env["project_id"],
        title,
        desc,
        criteria or ["criterion 1"],
        env["log_path"],
    )


def _get_ticket_status(conn, ticket_id):
    return conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()["status"]


def _get_ticket(conn, ticket_id):
    return dict(
        conn.execute(
            "SELECT id, project_id, title, description, status, "
            "current_cycle, current_impl_attempt, current_review_attempt "
            "FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
    )


def _run_impl_to_in_review(env, ticket_id=None):
    """Run implementation pipeline to get a ticket into in-review status."""
    if ticket_id is None:
        ticket_id = _add_ticket(env)
    ticket = _get_ticket(env["conn"], ticket_id)
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
    return ticket_id


# ---------------------------------------------------------------------------
# select_review_ticket
# ---------------------------------------------------------------------------


class TestSelectReviewTicket:
    def test_auto_select_in_review(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = select_review_ticket(env["conn"])
        assert ticket["id"] == tid
        assert ticket["status"] == "in-review"

    def test_explicit_ticket_id(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = select_review_ticket(env["conn"], tid)
        assert ticket["id"] == tid

    def test_explicit_ticket_wrong_status(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        with pytest.raises(ValueError, match="expected 'in-review'"):
            select_review_ticket(env["conn"], tid)

    def test_explicit_ticket_not_found(self, project_env):
        with pytest.raises(ValueError, match="not found"):
            select_review_ticket(project_env["conn"], "nonexistent")

    def test_no_in_review_tickets(self, project_env):
        with pytest.raises(ValueError, match="No ticket found"):
            select_review_ticket(project_env["conn"])


# ---------------------------------------------------------------------------
# Valid pass → human-gate
# ---------------------------------------------------------------------------


class TestReviewPass:
    def test_pass_transitions_to_human_gate(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final == "human-gate"
        assert _get_ticket_status(env["conn"], tid) == "human-gate"

    def test_pass_gate_reason_review_passed(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["gate_reason"] == "review_passed"

    def test_pass_medium_confidence_gate_reason(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="medium")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["gate_reason"] == "review_passed"

    def test_orchestrator_awaiting_human(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"

    def test_reviewer_run_recorded(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        runs = (
            env["conn"]
            .execute(
                "SELECT * FROM agent_runs WHERE ticket_id = ? AND role = 'reviewer'",
                (tid,),
            )
            .fetchall()
        )
        assert len(runs) == 1
        run = dict(runs[0])
        assert run["role"] == "reviewer"
        assert run["mode"] == "read-only"
        assert run["exit_status"] == "success"
        assert run["verdict"] == "pass"
        assert run["finished_at"] is not None


# ---------------------------------------------------------------------------
# Low-confidence pass → human-gate with low_confidence_pass
# ---------------------------------------------------------------------------


class TestLowConfidencePass:
    def test_low_confidence_gate_reason(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="low")

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final == "human-gate"
        row = (
            env["conn"]
            .execute("SELECT gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["gate_reason"] == "low_confidence_pass"


# ---------------------------------------------------------------------------
# Valid fail → revise
# ---------------------------------------------------------------------------


class TestReviewFail:
    def test_fail_transitions_to_revise(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        findings = [
            Finding(
                severity="blocking",
                category="correctness",
                description="Missing null check",
                location="impl.txt:1",
            )
        ]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final == "revise"
        assert _get_ticket_status(env["conn"], tid) == "revise"

    def test_findings_persisted(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        findings = [
            Finding(
                severity="blocking",
                category="correctness",
                description="Missing null check",
                location="impl.txt:1",
            )
        ]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        rows = (
            env["conn"]
            .execute("SELECT * FROM findings WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        f = dict(rows[0])
        assert f["severity"] == "blocking"
        assert f["category"] == "correctness"
        assert f["disposition"] == "open"

    def test_orchestrator_idle_after_revise(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        findings = [
            Finding(
                severity="blocking",
                category="correctness",
                description="Bug",
            )
        ]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_ac_updated_on_fail(self, project_env):
        env = project_env
        tid = _add_ticket_with_criteria(env, criteria=["Check null handling"])
        tid = _run_impl_to_in_review(env, tid)
        ticket = _get_ticket(env["conn"], tid)

        # Get the criterion ID
        crit = (
            env["conn"]
            .execute("SELECT id FROM acceptance_criteria WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        crit_id = crit["id"]

        findings = [
            Finding(
                severity="blocking",
                category="correctness",
                description="Missing null check",
                acceptance_criterion_id=crit_id,
            )
        ]
        scope = ScopeReviewed(
            files_examined=["impl.txt"],
            criteria_checked=[
                CriterionChecked(
                    criterion_id=crit_id, description="Check null handling"
                )
            ],
        )
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
            scope_reviewed=scope,
        )

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT status FROM acceptance_criteria WHERE id = ?", (crit_id,))
            .fetchone()
        )
        assert row["status"] == "unmet"


# ---------------------------------------------------------------------------
# Escalate → human-gate
# ---------------------------------------------------------------------------


class TestReviewEscalate:
    def test_escalate_transitions_to_human_gate(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="escalate", confidence="low")

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final == "human-gate"
        row = (
            env["conn"]
            .execute("SELECT gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["gate_reason"] == "reviewer_escalated"


# ---------------------------------------------------------------------------
# Contract violation → retry → blocked
# ---------------------------------------------------------------------------


class TestContractViolation:
    def test_contract_violation_blocked(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        # This adapter modifies files, triggering contract violation
        # Default max_review_retries=2, so 2 violations → blocked
        adapter = FileModifyingReviewAdapter(env["repo"])

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final == "blocked"
        assert _get_ticket_status(env["conn"], tid) == "blocked"

    def test_contract_violation_logged(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = FileModifyingReviewAdapter(env["repo"])

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "CONTRACT_VIOLATION" in log_content


# ---------------------------------------------------------------------------
# Parse error → retry → blocked
# ---------------------------------------------------------------------------


class TestParseError:
    def test_parse_error_blocked(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(exit_status="parse_error")

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final == "blocked"
        assert _get_ticket_status(env["conn"], tid) == "blocked"

    def test_parse_error_blocked_reason(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(exit_status="parse_error")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["blocked_reason"] == "reviewer_contract_violation"


# ---------------------------------------------------------------------------
# Workspace drift
# ---------------------------------------------------------------------------


class TestWorkspaceDrift:
    def test_drift_rejected_without_allow_drift(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)

        # Modify the workspace to create drift
        (env["repo"] / "impl.txt").write_text("drifted content\n")

        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        from capsaicin.review_baseline import WorkspaceDriftError

        with pytest.raises(WorkspaceDriftError):
            run_review_pipeline(
                env["conn"],
                env["project_id"],
                ticket,
                env["config"],
                adapter,
                log_path=env["log_path"],
            )

    def test_drift_accepted_with_allow_drift(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)

        # Modify the workspace to create drift
        (env["repo"] / "impl.txt").write_text("drifted content\n")

        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            allow_drift=True,
            log_path=env["log_path"],
        )

        assert final == "human-gate"


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    def test_pass_transitions_recorded(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
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
        assert ("in-review", "human-gate") in statuses

    def test_fail_transitions_recorded(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        findings = [Finding(severity="blocking", category="bug", description="Error")]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
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
        assert ("in-review", "revise") in statuses


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_log_events_written(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "RUN_START" in log_content
        assert "RUN_END" in log_content


# ---------------------------------------------------------------------------
# Adapter receives correct request
# ---------------------------------------------------------------------------


class TestAdapterRequest:
    def test_adapter_receives_reviewer_request(self, project_env):
        env = project_env
        tid = _add_ticket_with_criteria(env, title="Auth", desc="Add auth")
        tid = _run_impl_to_in_review(env, tid)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert len(adapter.calls) == 1
        req = adapter.calls[0]
        assert req.role == "reviewer"
        assert req.mode == "read-only"
        assert req.diff_context is not None
        assert len(req.diff_context) > 0
        assert "Auth" in req.prompt
        assert "Add auth" in req.prompt


# ---------------------------------------------------------------------------
# Review baseline recorded
# ---------------------------------------------------------------------------


class TestReviewBaseline:
    def test_baseline_captured(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockReviewAdapter(verdict="pass", confidence="high")

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        # Should have a review baseline for the reviewer run
        reviewer_run = (
            env["conn"]
            .execute(
                "SELECT id FROM agent_runs WHERE ticket_id = ? AND role = 'reviewer'",
                (tid,),
            )
            .fetchone()
        )

        baseline = (
            env["conn"]
            .execute(
                "SELECT * FROM review_baselines WHERE run_id = ?",
                (reviewer_run["id"],),
            )
            .fetchone()
        )
        assert baseline is not None
        assert baseline["baseline_status"] == "captured"


# ---------------------------------------------------------------------------
# Bulk-close findings on pass (re-review cycle)
# ---------------------------------------------------------------------------


class TestBulkCloseOnPass:
    def test_prior_findings_closed_on_pass(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)

        # First review: fail with findings
        findings = [Finding(severity="blocking", category="bug", description="Error")]
        fail_adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )
        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            fail_adapter,
            log_path=env["log_path"],
        )

        # Verify findings are open
        open_count = (
            env["conn"]
            .execute(
                "SELECT COUNT(*) FROM findings WHERE ticket_id = ? AND disposition = 'open'",
                (tid,),
            )
            .fetchone()[0]
        )
        assert open_count == 1

        # Simulate re-implementation: put ticket back in implementing then in-review
        # with a new diff
        env["conn"].execute(
            "UPDATE tickets SET status = 'in-review', current_cycle = 2 WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()

        # Second review: pass
        ticket = _get_ticket(env["conn"], tid)
        pass_adapter = MockReviewAdapter(verdict="pass", confidence="high")
        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            pass_adapter,
            log_path=env["log_path"],
        )

        # Prior findings should be closed
        open_count = (
            env["conn"]
            .execute(
                "SELECT COUNT(*) FROM findings WHERE ticket_id = ? AND disposition = 'open'",
                (tid,),
            )
            .fetchone()[0]
        )
        assert open_count == 0

        fixed_count = (
            env["conn"]
            .execute(
                "SELECT COUNT(*) FROM findings WHERE ticket_id = ? AND disposition = 'fixed'",
                (tid,),
            )
            .fetchone()[0]
        )
        assert fixed_count >= 1


# ---------------------------------------------------------------------------
# Cycle limit on fail → human-gate instead of revise
# ---------------------------------------------------------------------------


class TestCycleLimitOnFail:
    def test_fail_at_cycle_limit_goes_to_human_gate(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        # Set cycle to max (default max_cycles=3)
        env["conn"].execute("UPDATE tickets SET current_cycle = 3 WHERE id = ?", (tid,))
        env["conn"].commit()
        ticket = _get_ticket(env["conn"], tid)

        findings = [Finding(severity="blocking", category="bug", description="Error")]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final == "human-gate"
        assert _get_ticket_status(env["conn"], tid) == "human-gate"

    def test_fail_at_cycle_limit_gate_reason(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        env["conn"].execute("UPDATE tickets SET current_cycle = 3 WHERE id = ?", (tid,))
        env["conn"].commit()
        ticket = _get_ticket(env["conn"], tid)

        findings = [Finding(severity="blocking", category="bug", description="Error")]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["gate_reason"] == "cycle_limit"

    def test_fail_at_cycle_limit_findings_still_reconciled(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        env["conn"].execute("UPDATE tickets SET current_cycle = 3 WHERE id = ?", (tid,))
        env["conn"].commit()
        ticket = _get_ticket(env["conn"], tid)

        findings = [Finding(severity="blocking", category="bug", description="Error")]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        # Findings should still be persisted even though we hit cycle limit
        rows = (
            env["conn"]
            .execute("SELECT * FROM findings WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1

    def test_fail_under_cycle_limit_goes_to_revise(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        # cycle 1 is under limit of 3
        ticket = _get_ticket(env["conn"], tid)

        findings = [Finding(severity="blocking", category="bug", description="Error")]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        final = run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final == "revise"

    def test_fail_at_cycle_limit_orchestrator_awaiting_human(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        env["conn"].execute("UPDATE tickets SET current_cycle = 3 WHERE id = ?", (tid,))
        env["conn"].commit()
        ticket = _get_ticket(env["conn"], tid)

        findings = [Finding(severity="blocking", category="bug", description="Error")]
        adapter = MockReviewAdapter(
            verdict="fail",
            confidence="high",
            findings=findings,
        )

        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"
