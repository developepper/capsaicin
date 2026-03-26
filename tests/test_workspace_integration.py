"""End-to-end workspace isolation integration tests (T07).

Tests the full ticket pipeline (run -> review -> approve) with workspace
isolation enabled and disabled, using real git worktrees.
"""

from __future__ import annotations

import pytest

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import (
    CriterionChecked,
    Finding,
    ReviewResult,
    RunRequest,
    RunResult,
    ScopeReviewed,
)
from capsaicin.config import load_config
from capsaicin.ticket_approve import approve_ticket
from capsaicin.ticket_review import run_review_pipeline
from capsaicin.ticket_run import run_implementation_pipeline
from tests.adapters import DiffProducingAdapter, WorkspaceDiffAdapter
from tests.conftest import add_ticket, get_ticket, get_ticket_status
from tests.workspace_helpers import commit_setup, enable_workspace


# ---------------------------------------------------------------------------
# Mock reviewer adapter
# ---------------------------------------------------------------------------


class _PassReviewer(BaseAdapter):
    """Reviewer that returns a passing verdict."""

    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        criteria = []
        result = ReviewResult(
            verdict="pass",
            confidence="high",
            findings=[],
            scope_reviewed=ScopeReviewed(
                files_examined=["impl.txt"],
                criteria_checked=criteria,
            ),
        )
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="ok",
            raw_stderr="",
            structured_result=result,
        )


# ---------------------------------------------------------------------------
# Full pipeline with workspace isolation
# ---------------------------------------------------------------------------


class TestFullPipelineWithIsolation:
    """Run -> review -> approve with workspace.enabled=True."""

    def test_run_review_approve_with_isolation(self, project_env):
        env = project_env
        enable_workspace(env)
        commit_setup(env)
        config = load_config(env["project_dir"] / "config.toml")

        tid = add_ticket(env, criteria=["impl works"])
        ticket = get_ticket(env["conn"], tid)

        # --- Implementation ---
        impl_adapter = WorkspaceDiffAdapter()
        final = run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=config,
            adapter=impl_adapter,
            log_path=env["log_path"],
        )
        assert final == "in-review"
        assert len(impl_adapter.calls) == 1
        impl_wd = impl_adapter.calls[0].working_directory
        assert impl_wd != str(env["repo"]), "Impl should use worktree, not base repo"

        # --- Review ---
        ticket = get_ticket(env["conn"], tid)
        reviewer = _PassReviewer()
        final = run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=config,
            adapter=reviewer,
            log_path=env["log_path"],
        )
        assert final == "human-gate"
        assert len(reviewer.calls) == 1
        assert reviewer.calls[0].working_directory == impl_wd

        # --- Approve ---
        ticket = get_ticket(env["conn"], tid)
        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            impl_wd,
            log_path=env["log_path"],
            config=config,
        )
        assert get_ticket_status(env["conn"], tid) == "pr-ready"


# ---------------------------------------------------------------------------
# Full pipeline without workspace isolation (backward-compatibility)
# ---------------------------------------------------------------------------


class TestFullPipelineWithoutIsolation:
    """Run -> review -> approve with workspace.enabled=False (default)."""

    def test_run_review_approve_without_isolation(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["impl works"])
        ticket = get_ticket(env["conn"], tid)

        # --- Implementation ---
        impl_adapter = DiffProducingAdapter(env["repo"])
        final = run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=impl_adapter,
            log_path=env["log_path"],
        )
        assert final == "in-review"
        assert len(impl_adapter.calls) == 1
        assert impl_adapter.calls[0].working_directory == str(env["repo"])

        # --- Review ---
        ticket = get_ticket(env["conn"], tid)
        reviewer = _PassReviewer()
        final = run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=reviewer,
            log_path=env["log_path"],
        )
        assert final == "human-gate"
        assert reviewer.calls[0].working_directory == str(env["repo"])

        # --- Approve ---
        ticket = get_ticket(env["conn"], tid)
        approve_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            env["config"].project.repo_path,
            log_path=env["log_path"],
            config=env["config"],
        )
        assert get_ticket_status(env["conn"], tid) == "pr-ready"

        # No workspace rows should exist
        ws_count = env["conn"].execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
        assert ws_count == 0

    def test_impl_pipeline_shared_repo_path(self, project_env):
        """Implementation adapter receives the base repo path when isolation is off."""
        env = project_env
        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        assert adapter.calls[0].working_directory == str(env["repo"])


# ---------------------------------------------------------------------------
# Workspace failure modes
# ---------------------------------------------------------------------------


class TestWorkspaceFailureModes:
    """Workspace failures produce deterministic blocked outcomes."""

    def test_dirty_base_repo_raises_on_ready_ticket(self, project_env):
        """Dirty base repo blocks ticket via resolve_or_block.

        When a ticket is still in ``ready``, ``resolve_or_block`` transitions
        it to ``blocked`` so the operator can intervene.
        """
        env = project_env
        enable_workspace(env)
        # Intentionally do NOT commit_setup — repo is dirty due to config change.
        config = load_config(env["project_dir"] / "config.toml")

        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)
        adapter = WorkspaceDiffAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=config,
            adapter=adapter,
            log_path=env["log_path"],
        )

        updated = get_ticket(env["conn"], tid)
        assert updated["status"] == "blocked"

    def test_stale_workspace_blocks_review(self, project_env):
        """Missing worktree before review blocks the ticket."""
        env = project_env
        enable_workspace(env)
        commit_setup(env)
        config = load_config(env["project_dir"] / "config.toml")

        tid = add_ticket(env)
        ticket = get_ticket(env["conn"], tid)

        # Run impl to in-review
        adapter = WorkspaceDiffAdapter()
        final = run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=config,
            adapter=adapter,
            log_path=env["log_path"],
        )
        assert final == "in-review"

        # Break the worktree
        from tests.workspace_helpers import break_worktree

        wt_path = adapter.calls[0].working_directory
        break_worktree(env, wt_path)

        # Attempt review
        ticket = get_ticket(env["conn"], tid)
        reviewer = _PassReviewer()
        final = run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=config,
            adapter=reviewer,
            log_path=env["log_path"],
        )

        assert final == "blocked"
        row = (
            env["conn"]
            .execute("SELECT blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["blocked_reason"].startswith("workspace_")


# ---------------------------------------------------------------------------
# Workspace ID linkage on agent_runs (gap placeholder)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="workspace_id not yet threaded into run insertion")
class TestWorkspaceIdOnRuns:
    """Placeholder: agent_runs.workspace_id should be populated during runs."""

    def test_workspace_id_linked_on_impl_run(self, project_env):
        pass

    def test_workspace_id_null_without_isolation(self, project_env):
        pass
