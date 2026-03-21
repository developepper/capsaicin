"""Tests for the approval pipeline (T21)."""

from __future__ import annotations

import pytest

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import (
    ReviewResult,
    RunRequest,
    RunResult,
    ScopeReviewed,
)
from capsaicin.orchestrator import get_state
from capsaicin.ticket_approve import (
    WorkspaceMismatchError,
    approve_ticket,
    build_approval_summary,
    select_approve_ticket,
)
from capsaicin.ticket_run import run_implementation_pipeline
from capsaicin.ticket_review import run_review_pipeline
from tests.adapters import DiffProducingAdapter
from tests.conftest import add_ticket, get_ticket, get_ticket_status


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


class MockReviewAdapter(BaseAdapter):
    def __init__(self, verdict="pass", confidence="high", findings=None):
        self.verdict = verdict
        self.confidence = confidence
        self.findings = findings or []
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        review_result = ReviewResult(
            verdict=self.verdict,
            confidence=self.confidence,
            findings=self.findings,
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


def _run_to_human_gate(env, gate_reason="review_passed", confidence="high"):
    """Run impl + review to get a ticket into human-gate."""
    tid = add_ticket(env, criteria=["criterion 1"])
    ticket = get_ticket(env["conn"], tid)

    # Implementation
    impl_adapter = DiffProducingAdapter(env["repo"])
    run_implementation_pipeline(
        env["conn"],
        env["project_id"],
        ticket,
        env["config"],
        impl_adapter,
        log_path=env["log_path"],
    )

    # Review (pass -> human-gate)
    ticket = get_ticket(env["conn"], tid)
    review_adapter = MockReviewAdapter(verdict="pass", confidence=confidence)
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


def _run_to_human_gate_with_gate_reason(env, gate_reason):
    """Get a ticket to human-gate with a specific gate_reason."""
    tid = add_ticket(env, criteria=["criterion 1"])

    if gate_reason == "review_passed":
        return _run_to_human_gate(env, confidence="high")
    elif gate_reason == "low_confidence_pass":
        return _run_to_human_gate(env, confidence="low")
    elif gate_reason in ("reviewer_escalated", "cycle_limit"):
        # Run impl to get to in-review, then manually set human-gate
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
        # Manually transition to human-gate with the desired gate_reason
        env["conn"].execute(
            "UPDATE tickets SET status = 'human-gate', gate_reason = ? WHERE id = ?",
            (gate_reason, tid),
        )
        env["conn"].execute(
            "INSERT INTO state_transitions (ticket_id, from_status, to_status, "
            "triggered_by, reason, created_at) VALUES (?, 'in-review', 'human-gate', "
            "'system', ?, datetime('now'))",
            (tid, f"Test: {gate_reason}"),
        )
        env["conn"].commit()
        return tid

    raise ValueError(f"Unsupported gate_reason for test: {gate_reason}")


# ---------------------------------------------------------------------------
# select_approve_ticket
# ---------------------------------------------------------------------------


class TestSelectApproveTicket:
    def test_auto_select_human_gate(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = select_approve_ticket(env["conn"])
        assert ticket["id"] == tid
        assert ticket["status"] == "human-gate"

    def test_explicit_ticket_id(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = select_approve_ticket(env["conn"], tid)
        assert ticket["id"] == tid

    def test_explicit_ticket_wrong_status(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        with pytest.raises(ValueError, match="expected 'human-gate'"):
            select_approve_ticket(env["conn"], tid)

    def test_explicit_ticket_not_found(self, project_env):
        with pytest.raises(ValueError, match="not found"):
            select_approve_ticket(project_env["conn"], "nonexistent")

    def test_no_human_gate_tickets(self, project_env):
        with pytest.raises(ValueError, match="No ticket found"):
            select_approve_ticket(project_env["conn"])


# ---------------------------------------------------------------------------
# Approval succeeds — review_passed gate_reason
# ---------------------------------------------------------------------------


class TestApproveSuccess:
    def test_transitions_to_pr_ready(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
        )

        assert final == "pr-ready"
        assert get_ticket_status(env["conn"], tid) == "pr-ready"

    def test_orchestrator_idle(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_decision_recorded(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            rationale="Looks good",
            log_path=env["log_path"],
        )

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        d = dict(rows[0])
        assert d["decision"] == "approve"
        assert d["rationale"] == "Looks good"

    def test_state_transition_recorded(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
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
        assert ("human-gate", "pr-ready") in statuses

    def test_no_rationale_ok_for_review_passed(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        # Should not raise — rationale not required for review_passed
        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
        )
        assert final == "pr-ready"


# ---------------------------------------------------------------------------
# Rationale required for certain gate_reasons
# ---------------------------------------------------------------------------


class TestRationaleRequired:
    def test_cycle_limit_requires_rationale(self, project_env):
        env = project_env
        tid = _run_to_human_gate_with_gate_reason(env, "cycle_limit")
        ticket = get_ticket(env["conn"], tid)

        with pytest.raises(ValueError, match="Rationale is required"):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                env["config"].project.repo_path,
                log_path=env["log_path"],
            )

    def test_cycle_limit_with_rationale_succeeds(self, project_env):
        env = project_env
        tid = _run_to_human_gate_with_gate_reason(env, "cycle_limit")
        ticket = get_ticket(env["conn"], tid)

        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            rationale="Reviewed manually, acceptable",
            log_path=env["log_path"],
        )
        assert final == "pr-ready"

    def test_reviewer_escalated_requires_rationale(self, project_env):
        env = project_env
        tid = _run_to_human_gate_with_gate_reason(env, "reviewer_escalated")
        ticket = get_ticket(env["conn"], tid)

        with pytest.raises(ValueError, match="Rationale is required"):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                env["config"].project.repo_path,
                log_path=env["log_path"],
            )

    def test_low_confidence_pass_requires_rationale(self, project_env):
        env = project_env
        tid = _run_to_human_gate_with_gate_reason(env, "low_confidence_pass")
        ticket = get_ticket(env["conn"], tid)

        with pytest.raises(ValueError, match="Rationale is required"):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                env["config"].project.repo_path,
                log_path=env["log_path"],
            )

    def test_low_confidence_pass_with_rationale_succeeds(self, project_env):
        env = project_env
        tid = _run_to_human_gate_with_gate_reason(env, "low_confidence_pass")
        ticket = get_ticket(env["conn"], tid)

        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            rationale="Verified manually",
            log_path=env["log_path"],
        )
        assert final == "pr-ready"


# ---------------------------------------------------------------------------
# Workspace drift / mismatch
# ---------------------------------------------------------------------------


class TestWorkspaceMismatch:
    def test_drift_rejects_without_force(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        # Modify workspace to create drift
        (env["repo"] / "impl.txt").write_text("drifted after review\n")

        with pytest.raises(WorkspaceMismatchError):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                env["config"].project.repo_path,
                log_path=env["log_path"],
            )

    def test_force_overrides_drift(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        # Modify workspace to create drift
        (env["repo"] / "impl.txt").write_text("drifted after review\n")

        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            force=True,
            log_path=env["log_path"],
        )
        assert final == "pr-ready"

    def test_no_drift_succeeds(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        # No workspace modification — should succeed
        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
        )
        assert final == "pr-ready"

    def test_verifies_against_review_baseline_not_impl_diff(self, project_env):
        """Approval must verify against the reviewed diff, not the impl diff.

        When --allow-drift was used during review, the workspace at review
        time differs from the original implementation diff.  Approval should
        compare against what was reviewed (the review baseline), not the
        original implementer output.
        """
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        ticket = get_ticket(env["conn"], tid)

        # Implementation produces one version of impl.txt
        impl_adapter = DiffProducingAdapter(env["repo"], content="version1\n")
        run_implementation_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            impl_adapter,
            log_path=env["log_path"],
        )

        # Simulate workspace drift before review: someone edits impl.txt
        (env["repo"] / "impl.txt").write_text("version2\n")

        # Review with --allow-drift (re-captures the diff baseline)
        ticket = get_ticket(env["conn"], tid)
        review_adapter = MockReviewAdapter(verdict="pass", confidence="high")
        run_review_pipeline(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"],
            review_adapter,
            allow_drift=True,
            log_path=env["log_path"],
        )
        assert get_ticket_status(env["conn"], tid) == "human-gate"

        # Workspace still has "version2" — matches what was reviewed
        # Approval should succeed because workspace matches the review baseline
        ticket = get_ticket(env["conn"], tid)
        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
        )
        assert final == "pr-ready"


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_decision_logged(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "DECISION" in log_content


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------


class TestApprovalSummary:
    def test_summary_includes_ticket_info(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
        )

        summary = build_approval_summary(env["conn"], tid)
        assert "Test ticket" in summary
        assert "pr-ready" in summary
        assert "criterion 1" in summary
