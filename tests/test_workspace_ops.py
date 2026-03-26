"""Tests for workspace lifecycle command services (workspace_ops.py).

Covers:
- workspace_status: shared mode, worktree mode (no workspace, active, failed)
- workspace_recover: isolation disabled, happy path, ticket not found
- workspace_cleanup: isolation disabled, no workspace, already cleaned, happy path
"""

from __future__ import annotations

import subprocess

import pytest

from capsaicin.config import (
    AdapterConfig,
    Config,
    LimitsConfig,
    PathsConfig,
    ProjectConfig,
    ReviewerConfig,
    TicketSelectionConfig,
    WorkspaceConfig,
)
from capsaicin.db import get_connection, run_migrations

from capsaicin.app.commands.workspace_ops import (
    WorkspaceActionResult,
    WorkspaceStatusResult,
    workspace_cleanup,
    workspace_recover,
    workspace_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "readme.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _make_conn():
    conn = get_connection(":memory:")
    run_migrations(conn)
    return conn


def _insert_project(conn, project_id="p1", repo_path="/tmp"):
    conn.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
        (project_id, "test", repo_path),
    )
    conn.commit()


def _insert_ticket(conn, ticket_id="t1", project_id="p1"):
    conn.execute(
        "INSERT INTO tickets (id, project_id, title, description, status) "
        "VALUES (?, ?, 'Test', 'desc', 'ready')",
        (ticket_id, project_id),
    )
    conn.commit()


def _make_config(repo_path="/tmp", workspace_enabled=False):
    return Config(
        project=ProjectConfig(name="test", repo_path=str(repo_path)),
        implementer=AdapterConfig(backend="claude", command="claude"),
        reviewer=AdapterConfig(backend="claude", command="claude"),
        limits=LimitsConfig(),
        reviewer_policy=ReviewerConfig(),
        ticket_selection=TicketSelectionConfig(),
        paths=PathsConfig(),
        workspace=WorkspaceConfig(
            enabled=workspace_enabled,
            branch_prefix="capsaicin/",
            auto_cleanup=True,
        ),
    )


# ---------------------------------------------------------------------------
# workspace_status
# ---------------------------------------------------------------------------


class TestWorkspaceStatus:
    def test_shared_mode_when_disabled(self):
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)
        config = _make_config(workspace_enabled=False)

        result = workspace_status(conn, config, "t1")

        assert isinstance(result, WorkspaceStatusResult)
        assert result.isolation_mode == "shared"
        assert result.workspace_id is None
        assert result.ticket_id == "t1"

    def test_none_mode_no_workspace(self):
        """When isolation is enabled but no workspace exists, report 'none'."""
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)
        config = _make_config(workspace_enabled=True)

        result = workspace_status(conn, config, "t1")

        assert result.isolation_mode == "none"
        assert result.workspace_id is None

    def test_worktree_mode_with_active_workspace(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace

        ws = create_workspace(
            conn,
            repo,
            "p1",
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
            ticket_id="t1",
        )

        result = workspace_status(conn, config, "t1")

        assert result.isolation_mode == "worktree"
        assert result.workspace_id == ws.workspace_id
        assert result.status == "active"
        assert result.branch_name == "capsaicin/t1"

    def test_branch_mode_when_worktree_removed(self, tmp_path):
        """When workspace exists but worktree directory is gone, report 'branch'."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace, cleanup_workspace

        ws = create_workspace(
            conn,
            repo,
            "p1",
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=False
            ),
            ticket_id="t1",
        )
        # Clean up the worktree (but keep the branch via auto_cleanup=False)
        cleanup_workspace(
            conn,
            repo,
            ws.workspace_id,
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=False
            ),
        )

        result = workspace_status(conn, config, "t1")

        assert result.isolation_mode == "branch"
        assert result.workspace_id == ws.workspace_id
        assert result.branch_name == "capsaicin/t1"

    def test_none_mode_after_full_cleanup(self, tmp_path):
        """After cleanup with auto_cleanup=True, both worktree and branch are
        gone — report 'none', not 'branch'."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace, cleanup_workspace

        ws = create_workspace(
            conn,
            repo,
            "p1",
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
            ticket_id="t1",
        )
        cleanup_workspace(
            conn,
            repo,
            ws.workspace_id,
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
        )

        result = workspace_status(conn, config, "t1")

        assert result.isolation_mode == "none"
        assert result.workspace_id == ws.workspace_id
        assert result.status == "cleaned"

    def test_ticket_not_found_raises(self):
        conn = _make_conn()
        _insert_project(conn)
        config = _make_config()

        with pytest.raises(ValueError, match="Ticket not found"):
            workspace_status(conn, config, "nonexistent")

    def test_unregistered_worktree_dir_reports_branch_not_worktree(self, tmp_path):
        """When the worktree directory exists on disk but is no longer
        registered with git, report 'branch' (not 'worktree') so the
        operator sees the same state that would block execution."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace

        ws_cfg = WorkspaceConfig(
            enabled=True, branch_prefix="capsaicin/", auto_cleanup=False
        )
        ws = create_workspace(conn, repo, "p1", ws_cfg, ticket_id="t1")

        # Unregister the worktree from git but leave the directory on disk.
        subprocess.run(
            ["git", "worktree", "remove", "--force", ws.worktree_path],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        # Re-create the directory so is_dir() would be True
        from pathlib import Path

        Path(ws.worktree_path).mkdir(parents=True)

        result = workspace_status(conn, config, "t1")

        # Should NOT report "worktree" — the directory is orphaned
        assert result.isolation_mode == "branch"

    def test_orphaned_worktree_surfaces_validation_failure(self, tmp_path):
        """When a recorded active workspace has lost git registration,
        workspace_status should run validate_workspace and surface
        the failure reason — matching what would block execution."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace

        ws_cfg = WorkspaceConfig(
            enabled=True, branch_prefix="capsaicin/", auto_cleanup=False
        )
        ws = create_workspace(conn, repo, "p1", ws_cfg, ticket_id="t1")

        # Unregister the worktree from git but leave the directory on disk.
        subprocess.run(
            ["git", "worktree", "remove", "--force", ws.worktree_path],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        from pathlib import Path

        Path(ws.worktree_path).mkdir(parents=True)

        result = workspace_status(conn, config, "t1")

        # The validation path should detect the missing registration and
        # surface the same failure that would block automated execution.
        assert result.failure_reason == "missing_worktree"
        assert result.failure_detail is not None
        assert result.status == "failed"
        assert result.blocked_reason == "workspace_missing_worktree"

    def test_blocked_reason_surfaced(self):
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)
        conn.execute(
            "UPDATE tickets SET status = 'blocked', blocked_reason = 'deps' WHERE id = 't1'"
        )
        conn.commit()
        config = _make_config()

        result = workspace_status(conn, config, "t1")
        assert result.blocked_reason == "deps"


# ---------------------------------------------------------------------------
# workspace_recover
# ---------------------------------------------------------------------------


class TestWorkspaceRecover:
    def test_disabled_raises(self):
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)
        config = _make_config(workspace_enabled=False)

        with pytest.raises(ValueError, match="not enabled"):
            workspace_recover(conn, "p1", config, "t1")

    def test_ticket_not_found_raises(self):
        conn = _make_conn()
        _insert_project(conn)
        config = _make_config(workspace_enabled=True)

        with pytest.raises(ValueError, match="Ticket not found"):
            workspace_recover(conn, "p1", config, "nonexistent")

    def test_recover_creates_fresh_workspace(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        result = workspace_recover(conn, "p1", config, "t1")

        assert isinstance(result, WorkspaceActionResult)
        assert result.action == "recovered"
        assert result.workspace_id is not None

    def test_recover_preserves_valid_workspace(self, tmp_path):
        """A healthy active workspace should be reused, not torn down."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace

        ws = create_workspace(
            conn,
            repo,
            "p1",
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
            ticket_id="t1",
        )

        result = workspace_recover(conn, "p1", config, "t1")

        assert result.action == "recovered"
        # Should reuse the same workspace, not create a new one
        assert result.workspace_id == ws.workspace_id


# ---------------------------------------------------------------------------
# workspace_cleanup
# ---------------------------------------------------------------------------


class TestWorkspaceCleanup:
    def test_disabled_raises(self):
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)
        config = _make_config(workspace_enabled=False)

        with pytest.raises(ValueError, match="not enabled"):
            workspace_cleanup(conn, config, "t1")

    def test_ticket_not_found_raises(self):
        conn = _make_conn()
        _insert_project(conn)
        config = _make_config(workspace_enabled=True)

        with pytest.raises(ValueError, match="Ticket not found"):
            workspace_cleanup(conn, config, "nonexistent")

    def test_no_workspace_returns_cleaned(self):
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)
        config = _make_config(workspace_enabled=True)

        result = workspace_cleanup(conn, config, "t1")

        assert result.action == "cleaned"
        assert "No workspace" in result.detail

    def test_cleanup_active_workspace(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace

        ws = create_workspace(
            conn,
            repo,
            "p1",
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
            ticket_id="t1",
        )

        result = workspace_cleanup(conn, config, "t1")

        assert result.action == "cleaned"
        assert ws.workspace_id in result.detail

    def test_cleanup_dirty_worktree_returns_conflict(self, tmp_path):
        """Worktree with uncommitted changes must not be force-removed."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace
        from pathlib import Path

        ws = create_workspace(
            conn,
            repo,
            "p1",
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
            ticket_id="t1",
        )

        # Dirty the worktree with uncommitted changes.
        (Path(ws.worktree_path) / "dirty.txt").write_text("unsaved work\n")

        result = workspace_cleanup(conn, config, "t1")

        assert result.action == "failed"
        assert "uncommitted" in result.detail.lower()
        # The worktree directory should still exist (not deleted).
        assert Path(ws.worktree_path).is_dir()

    def test_already_cleaned_returns_cleaned(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn, repo_path=str(repo))
        _insert_ticket(conn)
        config = _make_config(repo_path=repo, workspace_enabled=True)

        from capsaicin.workspace import create_workspace, cleanup_workspace

        ws = create_workspace(
            conn,
            repo,
            "p1",
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
            ticket_id="t1",
        )
        cleanup_workspace(
            conn,
            repo,
            ws.workspace_id,
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
        )

        result = workspace_cleanup(conn, config, "t1")

        assert result.action == "cleaned"
        assert "already cleaned" in result.detail.lower()
