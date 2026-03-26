"""Tests for the approval pipeline (T21)."""

from __future__ import annotations

import pytest

from capsaicin.errors import (
    InvalidStatusError,
    NoEligibleTicketError,
    TicketNotFoundError,
)
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import (
    ReviewResult,
    RunRequest,
    RunResult,
    ScopeReviewed,
)
from capsaicin.orchestrator import get_state
from capsaicin.config import WorkspaceConfig
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
        with pytest.raises(InvalidStatusError, match="expected 'human-gate'"):
            select_approve_ticket(env["conn"], tid)

    def test_explicit_ticket_not_found(self, project_env):
        with pytest.raises(TicketNotFoundError, match="not found"):
            select_approve_ticket(project_env["conn"], "nonexistent")

    def test_no_human_gate_tickets(self, project_env):
        with pytest.raises(NoEligibleTicketError, match="No ticket found"):
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
            config=env["config"],
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
            config=env["config"],
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
            config=env["config"],
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
            config=env["config"],
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
            config=env["config"],
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
                config=env["config"],
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
            config=env["config"],
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
                config=env["config"],
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
                config=env["config"],
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
            config=env["config"],
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
                config=env["config"],
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
            config=env["config"],
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
            config=env["config"],
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
            config=env["config"],
        )
        assert final == "pr-ready"


# ---------------------------------------------------------------------------
# Divergence persistence (AC-2)
# ---------------------------------------------------------------------------


class TestDivergencePersistence:
    """Workspace divergence is stored as persisted state and logged."""

    def test_rejected_divergence_persisted(self, project_env):
        """When workspace drifts and approval is rejected, a divergence
        record is persisted with recovery_action='rejected'."""
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        (env["repo"] / "impl.txt").write_text("drifted\n")

        with pytest.raises(WorkspaceMismatchError):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                env["config"].project.repo_path,
                log_path=env["log_path"],
                config=env["config"],
            )

        row = (
            env["conn"]
            .execute("SELECT * FROM workspace_divergences WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        assert row is not None
        d = dict(row)
        assert d["divergence_type"] == "diff_mismatch"
        assert d["recovery_action"] == "rejected"
        assert d["expected_diff"] is not None
        assert d["actual_diff"] is not None
        assert d["detected_at"] is not None

    def test_force_override_divergence_persisted(self, project_env):
        """When --force overrides drift, a divergence record is persisted
        with recovery_action='force_override'."""
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        (env["repo"] / "impl.txt").write_text("drifted\n")

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            force=True,
            log_path=env["log_path"],
            config=env["config"],
        )

        row = (
            env["conn"]
            .execute("SELECT * FROM workspace_divergences WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        assert row is not None
        assert dict(row)["recovery_action"] == "force_override"

    def test_divergence_logged_to_activity(self, project_env):
        """WORKSPACE_DIVERGENCE event appears in the activity log."""
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        (env["repo"] / "impl.txt").write_text("drifted\n")

        with pytest.raises(WorkspaceMismatchError):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                env["config"].project.repo_path,
                log_path=env["log_path"],
                config=env["config"],
            )

        log_content = env["log_path"].read_text()
        assert "WORKSPACE_DIVERGENCE" in log_content
        assert "diff_mismatch" in log_content
        assert "rejected" in log_content

    def test_no_divergence_when_workspace_matches(self, project_env):
        """No divergence record is created when workspace matches."""
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
            config=env["config"],
        )

        count = (
            env["conn"]
            .execute(
                "SELECT COUNT(*) FROM workspace_divergences WHERE ticket_id = ?",
                (tid,),
            )
            .fetchone()[0]
        )
        assert count == 0


# ---------------------------------------------------------------------------
# Approval metadata (AC-3)
# ---------------------------------------------------------------------------


class TestApprovalMetadata:
    """Approval-time metadata captures branch, path, and commit."""

    def test_metadata_persisted_on_approval(self, project_env):
        """approval_metadata row is created with branch, path, and commit."""
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
            config=env["config"],
        )

        decision = (
            env["conn"]
            .execute("SELECT id FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        assert decision is not None

        meta = (
            env["conn"]
            .execute(
                "SELECT * FROM approval_metadata WHERE decision_id = ?",
                (decision["id"],),
            )
            .fetchone()
        )
        assert meta is not None
        m = dict(meta)
        assert m["branch_name"]  # non-empty
        assert m["worktree_path"]  # non-empty
        assert m["commit_ref"]  # non-empty
        assert m["created_at"] is not None

    def test_metadata_has_valid_commit_ref(self, project_env):
        """commit_ref is a valid git SHA."""
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
            config=env["config"],
        )

        decision = (
            env["conn"]
            .execute("SELECT id FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        meta = (
            env["conn"]
            .execute(
                "SELECT commit_ref FROM approval_metadata WHERE decision_id = ?",
                (decision["id"],),
            )
            .fetchone()
        )
        # Git SHA is 40 hex characters
        assert len(meta["commit_ref"]) == 40
        assert all(c in "0123456789abcdef" for c in meta["commit_ref"])

    def test_metadata_worktree_path_matches_repo(self, project_env):
        """worktree_path in metadata matches the repo path used."""
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
            config=env["config"],
        )

        decision = (
            env["conn"]
            .execute("SELECT id FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        meta = (
            env["conn"]
            .execute(
                "SELECT worktree_path FROM approval_metadata WHERE decision_id = ?",
                (decision["id"],),
            )
            .fetchone()
        )
        assert meta["worktree_path"] == env["config"].project.repo_path

    def test_metadata_persisted_even_with_force_override(self, project_env):
        """Approval metadata is captured even when --force overrides drift."""
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        (env["repo"] / "impl.txt").write_text("drifted\n")

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            force=True,
            log_path=env["log_path"],
            config=env["config"],
        )

        decision = (
            env["conn"]
            .execute("SELECT id FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        meta = (
            env["conn"]
            .execute(
                "SELECT * FROM approval_metadata WHERE decision_id = ?",
                (decision["id"],),
            )
            .fetchone()
        )
        assert meta is not None
        assert dict(meta)["commit_ref"]


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
            config=env["config"],
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
            config=env["config"],
        )

        summary = build_approval_summary(env["conn"], tid)
        assert "Test ticket" in summary
        assert "pr-ready" in summary
        assert "criterion 1" in summary


# ---------------------------------------------------------------------------
# Workspace isolation enabled path
# ---------------------------------------------------------------------------


def _enable_workspace_isolation(env):
    """Return a copy of config with workspace isolation enabled."""
    from tests.workspace_helpers import enable_workspace_config

    return enable_workspace_config(env["config"])


def _insert_workspace(
    conn, project_id, ticket_id, worktree_path, branch_name, status="active"
):
    """Insert a workspace row for testing."""
    from capsaicin.queries import generate_id, now_utc

    ws_id = generate_id()
    conn.execute(
        "INSERT INTO workspaces "
        "(id, project_id, ticket_id, worktree_path, branch_name, "
        "base_ref, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ws_id,
            project_id,
            ticket_id,
            str(worktree_path),
            branch_name,
            "abc123",
            status,
            now_utc(),
            now_utc(),
        ),
    )
    conn.commit()
    return ws_id


class TestWorkspaceIsolationEnabled:
    """Tests for the workspace.enabled=True code path."""

    def test_active_workspace_with_matching_diff_succeeds(self, project_env):
        """When workspace isolation is enabled and the active workspace
        worktree matches the reviewed diff, approval succeeds."""
        env = project_env
        config = _enable_workspace_isolation(env)
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        # Create an active workspace pointing at the real repo
        _insert_workspace(
            env["conn"],
            env["project_id"],
            tid,
            env["repo"],
            "capsaicin/test-branch",
            status="active",
        )

        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            config.project.repo_path,
            log_path=env["log_path"],
            config=config,
        )
        assert final == "pr-ready"

    def test_workspace_invalid_divergence_when_not_active(self, project_env):
        """A workspace with non-active status produces workspace_invalid
        divergence and rejects without --force."""
        env = project_env
        config = _enable_workspace_isolation(env)
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        # Create a workspace in 'tearing_down' status
        ws_id = _insert_workspace(
            env["conn"],
            env["project_id"],
            tid,
            env["repo"],
            "capsaicin/test-branch",
            status="tearing_down",
        )

        with pytest.raises(WorkspaceMismatchError):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                config.project.repo_path,
                log_path=env["log_path"],
                config=config,
            )

        # Verify divergence was persisted as workspace_invalid
        row = (
            env["conn"]
            .execute("SELECT * FROM workspace_divergences WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        assert row is not None
        assert dict(row)["divergence_type"] == "workspace_invalid"
        assert dict(row)["recovery_action"] == "rejected"
        assert dict(row)["workspace_id"] == ws_id

    def test_workspace_invalid_when_worktree_path_missing(self, project_env):
        """A workspace pointing to a non-existent directory produces
        workspace_invalid divergence."""
        env = project_env
        config = _enable_workspace_isolation(env)
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        # Create workspace pointing to path that doesn't exist
        _insert_workspace(
            env["conn"],
            env["project_id"],
            tid,
            "/tmp/nonexistent-worktree-path",
            "capsaicin/test-branch",
            status="active",
        )

        with pytest.raises(WorkspaceMismatchError):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                config.project.repo_path,
                log_path=env["log_path"],
                config=config,
            )

        row = (
            env["conn"]
            .execute(
                "SELECT divergence_type FROM workspace_divergences WHERE ticket_id = ?",
                (tid,),
            )
            .fetchone()
        )
        assert row is not None
        assert row["divergence_type"] == "workspace_invalid"

    def test_force_overrides_workspace_invalid(self, project_env):
        """--force allows approval even when workspace is invalid."""
        env = project_env
        config = _enable_workspace_isolation(env)
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        _insert_workspace(
            env["conn"],
            env["project_id"],
            tid,
            env["repo"],
            "capsaicin/test-branch",
            status="tearing_down",
        )

        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            config.project.repo_path,
            force=True,
            log_path=env["log_path"],
            config=config,
        )
        assert final == "pr-ready"

        row = (
            env["conn"]
            .execute(
                "SELECT recovery_action FROM workspace_divergences WHERE ticket_id = ?",
                (tid,),
            )
            .fetchone()
        )
        assert row is not None
        assert row["recovery_action"] == "force_override"

    def test_workspace_invalid_logged_to_activity(self, project_env):
        """WORKSPACE_DIVERGENCE event with workspace_invalid type appears in log."""
        env = project_env
        config = _enable_workspace_isolation(env)
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        _insert_workspace(
            env["conn"],
            env["project_id"],
            tid,
            env["repo"],
            "capsaicin/test-branch",
            status="tearing_down",
        )

        with pytest.raises(WorkspaceMismatchError):
            approve_ticket(
                env["conn"],
                env["project_id"],
                ticket,
                config.project.repo_path,
                log_path=env["log_path"],
                config=config,
            )

        log_content = env["log_path"].read_text()
        assert "WORKSPACE_DIVERGENCE" in log_content
        assert "workspace_invalid" in log_content

    def test_no_workspace_falls_back_to_base_repo(self, project_env):
        """When workspace isolation is enabled but no workspace exists,
        falls back to base repo diff check and succeeds if matching."""
        env = project_env
        config = _enable_workspace_isolation(env)
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        # No workspace inserted — should fall back to base repo path
        final = approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            config.project.repo_path,
            log_path=env["log_path"],
            config=config,
        )
        assert final == "pr-ready"

    def test_metadata_uses_workspace_branch(self, project_env):
        """When an active workspace exists, approval metadata captures
        the workspace branch and worktree path."""
        env = project_env
        config = _enable_workspace_isolation(env)
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        ws_id = _insert_workspace(
            env["conn"],
            env["project_id"],
            tid,
            env["repo"],
            "capsaicin/feature-branch",
            status="active",
        )

        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            config.project.repo_path,
            log_path=env["log_path"],
            config=config,
        )

        decision = (
            env["conn"]
            .execute("SELECT id FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        meta = (
            env["conn"]
            .execute(
                "SELECT * FROM approval_metadata WHERE decision_id = ?",
                (decision["id"],),
            )
            .fetchone()
        )
        m = dict(meta)
        assert m["workspace_id"] == ws_id
        assert m["branch_name"] == "capsaicin/feature-branch"
        assert m["worktree_path"] == str(env["repo"])
