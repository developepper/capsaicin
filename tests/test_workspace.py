"""Tests for the git workspace manager (workspace.py)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from capsaicin.config import WorkspaceConfig
from capsaicin.workspace import (
    RecoveryAction,
    SetupFailure,
    SetupSuccess,
    WorkspaceRecovery,
    WorkspaceReady,
    acquire_workspace,
    create_workspace,
    run_setup_commands,
    validate_workspace,
)
from tests.workspace_helpers import (
    default_ws_config as _default_ws_config,
    init_git_repo as _init_git_repo,
    insert_epic as _insert_epic,
    insert_project as _insert_project,
    insert_ticket as _insert_ticket,
    make_workspace_conn as _make_conn,
)


# ---------------------------------------------------------------------------
# AC-1: Create and validate isolated workspace
# ---------------------------------------------------------------------------


class TestCreateWorkspace:
    """AC-1: create and validate an isolated execution workspace."""

    def test_create_workspace_happy_path(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        result = create_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )

        assert isinstance(result, WorkspaceReady)
        assert result.branch_name == "capsaicin/t1"
        assert "t1" in result.worktree_path

        # Worktree actually exists on disk.
        from pathlib import Path

        assert Path(result.worktree_path).is_dir()

        # DB row is active.
        row = conn.execute(
            "SELECT status FROM workspaces WHERE id = ?", (result.workspace_id,)
        ).fetchone()
        assert row["status"] == "active"

    def test_create_workspace_for_epic(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_epic(conn)

        result = create_workspace(conn, repo, "p1", _default_ws_config(), epic_id="e1")

        assert isinstance(result, WorkspaceReady)
        assert result.branch_name == "capsaicin/e1"

    def test_does_not_mutate_active_checkout(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        # Record the active branch before workspace creation.
        before = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        create_workspace(conn, repo, "p1", _default_ws_config(), ticket_id="t1")

        after = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert before == after

    def test_dirty_base_repo_returns_recovery(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        # Dirty the repo.
        (repo / "dirty.txt").write_text("uncommitted\n")

        result = create_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )

        assert isinstance(result, WorkspaceRecovery)
        assert result.failure_reason == "dirty_base_repo"
        assert result.action == RecoveryAction.retry

        # DB row is failed.
        row = conn.execute(
            "SELECT status, failure_reason FROM workspaces WHERE id = ?",
            (result.workspace_id,),
        ).fetchone()
        assert row["status"] == "failed"
        assert row["failure_reason"] == "dirty_base_repo"

    def test_branch_already_exists_returns_branch_drift(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn, ticket_id="t1")
        _insert_ticket(conn, ticket_id="t2")

        # Pre-create the branch that create_workspace would try to use.
        subprocess.run(
            ["git", "branch", "capsaicin/t2"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = create_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t2"
        )

        assert isinstance(result, WorkspaceRecovery)
        assert result.failure_reason == "branch_drift"
        assert result.action == RecoveryAction.recreate

    def test_requires_ticket_or_epic(self):
        conn = _make_conn()
        _insert_project(conn)

        with pytest.raises(ValueError, match="Either ticket_id or epic_id"):
            create_workspace(conn, "/tmp", "p1", _default_ws_config())

    def test_rejects_both_ticket_and_epic(self):
        conn = _make_conn()
        _insert_project(conn)

        with pytest.raises(ValueError, match="Only one"):
            create_workspace(
                conn,
                "/tmp",
                "p1",
                _default_ws_config(),
                ticket_id="t1",
                epic_id="e1",
            )

    def test_custom_branch_prefix(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        cfg = WorkspaceConfig(enabled=True, branch_prefix="work/", auto_cleanup=True)
        result = create_workspace(conn, repo, "p1", cfg, ticket_id="t1")

        assert isinstance(result, WorkspaceReady)
        assert result.branch_name == "work/t1"


# ---------------------------------------------------------------------------
# AC-2: Typed recovery outcomes for validation mismatches
# ---------------------------------------------------------------------------


class TestValidateWorkspace:
    """AC-2: validation returns typed recovery when metadata mismatches state."""

    def test_valid_workspace_returns_ready(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        created = create_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )
        assert isinstance(created, WorkspaceReady)

        result = validate_workspace(conn, repo, created.workspace_id)
        assert isinstance(result, WorkspaceReady)
        assert result.workspace_id == created.workspace_id

    def test_missing_worktree_returns_recovery(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        created = create_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )
        assert isinstance(created, WorkspaceReady)

        # Remove the worktree directory (simulate external deletion).
        subprocess.run(
            ["git", "worktree", "remove", "--force", created.worktree_path],
            cwd=repo,
            capture_output=True,
        )
        shutil.rmtree(created.worktree_path, ignore_errors=True)

        result = validate_workspace(conn, repo, created.workspace_id)
        assert isinstance(result, WorkspaceRecovery)
        assert result.failure_reason == "missing_worktree"
        assert result.action == RecoveryAction.recreate

    def test_branch_drift_returns_recovery(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        created = create_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )
        assert isinstance(created, WorkspaceReady)

        # Advance HEAD in the base repo (simulates upstream movement).
        (repo / "new_file.txt").write_text("new\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "advance"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = validate_workspace(conn, repo, created.workspace_id)
        assert isinstance(result, WorkspaceRecovery)
        assert result.failure_reason == "branch_drift"
        assert result.action == RecoveryAction.recreate

    def test_already_failed_workspace_surfaces_recovery(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        # Dirty repo to get a failed workspace.
        (repo / "dirty.txt").write_text("d\n")
        created = create_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )
        assert isinstance(created, WorkspaceRecovery)

        result = validate_workspace(conn, repo, created.workspace_id)
        assert isinstance(result, WorkspaceRecovery)
        assert result.failure_reason == "dirty_base_repo"

    def test_nonexistent_workspace_raises(self):
        conn = _make_conn()
        with pytest.raises(KeyError, match="No workspace found"):
            validate_workspace(conn, "/tmp", "nonexistent")

    def test_recovery_action_mapping(self):
        """Each failure_reason maps to the expected RecoveryAction."""
        r = WorkspaceRecovery.for_reason("w1", "dirty_base_repo", "d")
        assert r.action == RecoveryAction.retry

        r = WorkspaceRecovery.for_reason("w1", "missing_worktree", "d")
        assert r.action == RecoveryAction.recreate

        r = WorkspaceRecovery.for_reason("w1", "branch_drift", "d")
        assert r.action == RecoveryAction.recreate

        r = WorkspaceRecovery.for_reason("w1", "setup_failure", "d")
        assert r.action == RecoveryAction.retry

        r = WorkspaceRecovery.for_reason("w1", "cleanup_conflict", "d")
        assert r.action == RecoveryAction.human_gate


# ---------------------------------------------------------------------------
# AC-1 (continued): Reuse existing workspace via acquire_workspace
# ---------------------------------------------------------------------------


class TestAcquireWorkspace:
    """acquire_workspace reuses existing active workspaces or creates new ones."""

    def test_creates_when_none_exists(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        result = acquire_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )
        assert isinstance(result, WorkspaceReady)

    def test_reuses_active_workspace(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        first = create_workspace(conn, repo, "p1", _default_ws_config(), ticket_id="t1")
        assert isinstance(first, WorkspaceReady)

        second = acquire_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )
        assert isinstance(second, WorkspaceReady)
        assert second.workspace_id == first.workspace_id

    def test_creates_new_after_failed(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        # Dirty repo to get a failed workspace.
        (repo / "dirty.txt").write_text("d\n")
        failed = create_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )
        assert isinstance(failed, WorkspaceRecovery)

        # Clean up and acquire again — should create a new one.
        subprocess.run(["git", "checkout", "--", "."], cwd=repo, capture_output=True)
        # Remove untracked dirty.txt
        (repo / "dirty.txt").unlink(missing_ok=True)

        result = acquire_workspace(
            conn, repo, "p1", _default_ws_config(), ticket_id="t1"
        )
        assert isinstance(result, WorkspaceReady)
        assert result.workspace_id != failed.workspace_id


# ---------------------------------------------------------------------------
# AC-3: Setup commands execute in isolated workspace
# ---------------------------------------------------------------------------


class TestRunSetupCommands:
    """AC-3: setup commands execute in the workspace and failures are persisted."""

    def test_successful_setup(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(conn, repo, "p1", _default_ws_config(), ticket_id="t1")
        assert isinstance(ws, WorkspaceReady)

        result = run_setup_commands(conn, ws.workspace_id, ["echo hello", "true"])
        assert isinstance(result, SetupSuccess)
        assert result.workspace_id == ws.workspace_id

        # Workspace should still be active (not transitioned to failed).
        row = conn.execute(
            "SELECT status FROM workspaces WHERE id = ?", (ws.workspace_id,)
        ).fetchone()
        assert row["status"] == "active"

    def test_failed_setup_persists_detail(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(conn, repo, "p1", _default_ws_config(), ticket_id="t1")
        assert isinstance(ws, WorkspaceReady)

        result = run_setup_commands(conn, ws.workspace_id, ["false"])
        assert isinstance(result, SetupFailure)
        assert result.command == "false"
        assert result.exit_code != 0

        # DB row is failed with detail.
        row = conn.execute(
            "SELECT status, failure_reason, failure_detail FROM workspaces WHERE id = ?",
            (ws.workspace_id,),
        ).fetchone()
        assert row["status"] == "failed"
        assert row["failure_reason"] == "setup_failure"
        assert "false" in row["failure_detail"]

    def test_stops_at_first_failure(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(conn, repo, "p1", _default_ws_config(), ticket_id="t1")
        assert isinstance(ws, WorkspaceReady)

        # Second command should never run.
        result = run_setup_commands(
            conn,
            ws.workspace_id,
            ["false", "touch should_not_exist.txt"],
        )
        assert isinstance(result, SetupFailure)

        from pathlib import Path

        assert not (Path(ws.worktree_path) / "should_not_exist.txt").exists()

    def test_commands_run_in_worktree_directory(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(conn, repo, "p1", _default_ws_config(), ticket_id="t1")
        assert isinstance(ws, WorkspaceReady)

        # Create a marker file via setup command.
        result = run_setup_commands(conn, ws.workspace_id, ["touch marker.txt"])
        assert isinstance(result, SetupSuccess)

        from pathlib import Path

        # Marker should be in the worktree, not the base repo.
        assert (Path(ws.worktree_path) / "marker.txt").exists()
        assert not (repo / "marker.txt").exists()

    def test_empty_command_list_succeeds(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(conn, repo, "p1", _default_ws_config(), ticket_id="t1")
        assert isinstance(ws, WorkspaceReady)

        result = run_setup_commands(conn, ws.workspace_id, [])
        assert isinstance(result, SetupSuccess)

    def test_nonexistent_workspace_raises(self):
        conn = _make_conn()
        with pytest.raises(KeyError, match="No workspace found"):
            run_setup_commands(conn, "nonexistent", ["echo hi"])

    def test_setup_failure_stderr_captured(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(conn, repo, "p1", _default_ws_config(), ticket_id="t1")
        assert isinstance(ws, WorkspaceReady)

        result = run_setup_commands(
            conn, ws.workspace_id, ["echo error_msg >&2 && exit 1"]
        )
        assert isinstance(result, SetupFailure)
        assert "error_msg" in result.stderr
