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
    cleanup_workspace,
    create_workspace,
    recover_workspace,
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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
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

        result = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), epic_id="e1"
        )

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

        create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )

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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t2"
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

        cfg = WorkspaceConfig(
            enabled=True,
            branch_prefix="work/",
            auto_cleanup=True,
            worktree_root=str(tmp_path / "wt"),
        )
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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(result, WorkspaceReady)

    def test_reuses_active_workspace(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        first = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(first, WorkspaceReady)

        second = acquire_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
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
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(failed, WorkspaceRecovery)

        # Clean up and acquire again — should create a new one.
        subprocess.run(["git", "checkout", "--", "."], cwd=repo, capture_output=True)
        # Remove untracked dirty.txt
        (repo / "dirty.txt").unlink(missing_ok=True)

        result = acquire_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
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

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
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

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
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

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
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

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
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

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(ws, WorkspaceReady)

        result = run_setup_commands(conn, ws.workspace_id, [])
        assert isinstance(result, SetupSuccess)

    def test_nonexistent_workspace_raises(self):
        conn = _make_conn()
        with pytest.raises(KeyError, match="No workspace found"):
            run_setup_commands(conn, "nonexistent", ["echo hi"])

    def test_shell_metacharacters_not_executed(self, tmp_path):
        """Regression: shell metacharacters must be treated as literal args."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(ws, WorkspaceReady)

        from pathlib import Path

        marker = Path(ws.worktree_path) / "pwned.txt"

        # The injected portion after ';' must NOT execute.
        result = run_setup_commands(
            conn, ws.workspace_id, ["echo safe ; touch pwned.txt"]
        )

        # shlex.split produces ["echo", "safe", ";", "touch", "pwned.txt"],
        # which echo prints as literal args — the touch never runs.
        assert not marker.exists(), "Shell injection executed the injected command"

    def test_setup_failure_stderr_captured(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(ws, WorkspaceReady)

        result = run_setup_commands(
            conn, ws.workspace_id, ["bash -c 'echo error_msg >&2 && exit 1'"]
        )
        assert isinstance(result, SetupFailure)
        assert "error_msg" in result.stderr


# ---------------------------------------------------------------------------
# Transactional workspace creation (AC5: rollback on interruption)
# ---------------------------------------------------------------------------


class TestTransactionalCreate:
    """Verify that create_workspace commits only once, at the end."""

    def test_no_committed_row_before_worktree_add(self, tmp_path):
        """If create_workspace is interrupted after the INSERT but before the
        git worktree add completes, no committed row should exist because
        create_workspace itself rolls back the transaction."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        from unittest.mock import patch

        original_git = __import__("capsaicin.workspace", fromlist=["_git"])._git

        def interrupting_git(args, cwd, timeout=30):
            # The worktree add call is the one we want to interrupt.
            if args[0] == "worktree" and args[1] == "add":
                raise KeyboardInterrupt("simulated crash")
            return original_git(args, cwd, timeout)

        with patch("capsaicin.workspace._git", side_effect=interrupting_git):
            try:
                create_workspace(
                    conn,
                    repo,
                    "p1",
                    _default_ws_config(tmp_path / "wt"),
                    ticket_id="t1",
                )
            except KeyboardInterrupt:
                pass

        # create_workspace rolled back on interruption — the row is gone
        # without any manual rollback from the caller.
        row = conn.execute("SELECT * FROM workspaces WHERE ticket_id = 't1'").fetchone()
        assert row is None, (
            "Row should not exist after interrupted create (auto-rollback)"
        )

        # An unrelated commit on the same connection must not resurface the row.
        conn.execute("SELECT 1")
        conn.commit()
        row = conn.execute("SELECT * FROM workspaces WHERE ticket_id = 't1'").fetchone()
        assert row is None, "Row must not reappear after unrelated commit"

    def test_no_committed_row_when_preflight_interrupted(self, tmp_path):
        """If create_workspace is interrupted during the preflight check
        (before worktree add), the uncommitted insert is rolled back."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        from unittest.mock import patch

        def interrupting_clean_check(path):
            raise KeyboardInterrupt("simulated crash during preflight")

        with patch(
            "capsaicin.workspace._working_tree_is_clean",
            side_effect=interrupting_clean_check,
        ):
            try:
                create_workspace(
                    conn,
                    repo,
                    "p1",
                    _default_ws_config(tmp_path / "wt"),
                    ticket_id="t1",
                )
            except KeyboardInterrupt:
                pass

        row = conn.execute("SELECT * FROM workspaces WHERE ticket_id = 't1'").fetchone()
        assert row is None, "Row should not exist after interrupted preflight"

        # An unrelated commit must not resurface the row.
        conn.execute("SELECT 1")
        conn.commit()
        row = conn.execute("SELECT * FROM workspaces WHERE ticket_id = 't1'").fetchone()
        assert row is None, "Row must not reappear after unrelated commit"

    def test_success_commits_all_at_once(self, tmp_path):
        """On success, both the INSERT and the active status are visible
        after a single commit at the end — not incrementally."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        result = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(result, WorkspaceReady)

        # The row is active (both insert and status update committed together).
        row = conn.execute(
            "SELECT status FROM workspaces WHERE id = ?", (result.workspace_id,)
        ).fetchone()
        assert row["status"] == "active"

    def test_no_committed_row_when_branch_check_interrupted(self, tmp_path):
        """If create_workspace is interrupted during _branch_exists (after a
        failed git worktree add), the uncommitted insert is rolled back."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        from unittest.mock import patch
        import subprocess

        original_git = __import__("capsaicin.workspace", fromlist=["_git"])._git

        def failing_git(args, cwd, timeout=30):
            if args[0] == "worktree" and args[1] == "add":
                return subprocess.CompletedProcess(args, 1, "", "error")
            return original_git(args, cwd, timeout)

        def interrupting_branch_exists(repo, branch):
            raise KeyboardInterrupt("simulated crash during branch check")

        with (
            patch("capsaicin.workspace._git", side_effect=failing_git),
            patch(
                "capsaicin.workspace._branch_exists",
                side_effect=interrupting_branch_exists,
            ),
        ):
            try:
                create_workspace(
                    conn,
                    repo,
                    "p1",
                    _default_ws_config(tmp_path / "wt"),
                    ticket_id="t1",
                )
            except KeyboardInterrupt:
                pass

        row = conn.execute("SELECT * FROM workspaces WHERE ticket_id = 't1'").fetchone()
        assert row is None, "Row should not exist after interrupted branch check"

        # An unrelated commit must not resurface the row.
        conn.execute("SELECT 1")
        conn.commit()
        row = conn.execute("SELECT * FROM workspaces WHERE ticket_id = 't1'").fetchone()
        assert row is None, "Row must not reappear after unrelated commit"

    def test_failure_commits_failed_status(self, tmp_path):
        """On failure, the committed row has status=failed (not pending/setting_up)."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        (repo / "dirty.txt").write_text("uncommitted\n")

        result = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(result, WorkspaceRecovery)

        row = conn.execute(
            "SELECT status FROM workspaces WHERE id = ?", (result.workspace_id,)
        ).fetchone()
        assert row["status"] == "failed"


# ---------------------------------------------------------------------------
# Transactional cleanup (AC2: single commit)
# ---------------------------------------------------------------------------


class TestTransactionalCleanup:
    def test_cleanup_rollback_on_interruption(self, tmp_path):
        """If cleanup_workspace is interrupted during git operations,
        the tearing_down status is rolled back — not left pending."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(ws, WorkspaceReady)

        from unittest.mock import patch

        original_git = __import__("capsaicin.workspace", fromlist=["_git"])._git

        def interrupting_git(args, cwd, timeout=30):
            if args[0] == "worktree" and args[1] == "remove":
                raise KeyboardInterrupt("simulated crash during teardown")
            return original_git(args, cwd, timeout)

        with patch("capsaicin.workspace._git", side_effect=interrupting_git):
            try:
                cleanup_workspace(
                    conn,
                    repo,
                    ws.workspace_id,
                    _default_ws_config(tmp_path / "wt"),
                )
            except KeyboardInterrupt:
                pass

        # The tearing_down status should have been rolled back — the
        # workspace should still show its pre-cleanup status (active).
        row = conn.execute(
            "SELECT status FROM workspaces WHERE id = ?", (ws.workspace_id,)
        ).fetchone()
        assert row["status"] == "active", (
            f"Expected 'active' after rollback, got '{row['status']}'"
        )

        # An unrelated commit must not persist tearing_down.
        conn.execute("SELECT 1")
        conn.commit()
        row = conn.execute(
            "SELECT status FROM workspaces WHERE id = ?", (ws.workspace_id,)
        ).fetchone()
        assert row["status"] == "active", (
            "tearing_down must not reappear after unrelated commit"
        )

    def test_cleanup_commits_cleaned_status(self, tmp_path):
        """After cleanup, the committed row has status=cleaned."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws = create_workspace(
            conn, repo, "p1", _default_ws_config(tmp_path / "wt"), ticket_id="t1"
        )
        assert isinstance(ws, WorkspaceReady)

        result = cleanup_workspace(
            conn, repo, ws.workspace_id, _default_ws_config(tmp_path / "wt")
        )
        assert result is None

        row = conn.execute(
            "SELECT status FROM workspaces WHERE id = ?", (ws.workspace_id,)
        ).fetchone()
        assert row["status"] == "cleaned"


# ---------------------------------------------------------------------------
# AC6/AC7: recover_workspace handles stuck intermediate states
# ---------------------------------------------------------------------------


class TestRecoverStuckWorkspace:
    def test_recover_setting_up_workspace(self, tmp_path):
        """A workspace stuck in setting_up should be cleaned up and recreated."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws_cfg = _default_ws_config(tmp_path / "wt")

        # Create a workspace, then manually set it to setting_up to simulate
        # a stuck intermediate state.
        ws = create_workspace(conn, repo, "p1", ws_cfg, ticket_id="t1")
        assert isinstance(ws, WorkspaceReady)
        conn.execute(
            "UPDATE workspaces SET status = 'setting_up', "
            "failure_reason = NULL, failure_detail = NULL WHERE id = ?",
            (ws.workspace_id,),
        )
        conn.commit()

        result = recover_workspace(conn, repo, "p1", ws_cfg, ticket_id="t1")

        assert isinstance(result, WorkspaceReady)
        # Should be a new workspace, not the stuck one.
        assert result.workspace_id != ws.workspace_id

    def test_recover_tearing_down_workspace(self, tmp_path):
        """A workspace stuck in tearing_down should have teardown completed
        without provisioning a new workspace."""
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        conn = _make_conn()
        _insert_project(conn)
        _insert_ticket(conn)

        ws_cfg = _default_ws_config(tmp_path / "wt")

        # Create a workspace, then manually set it to tearing_down.
        ws = create_workspace(conn, repo, "p1", ws_cfg, ticket_id="t1")
        assert isinstance(ws, WorkspaceReady)
        conn.execute(
            "UPDATE workspaces SET status = 'tearing_down', "
            "failure_reason = NULL, failure_detail = NULL WHERE id = ?",
            (ws.workspace_id,),
        )
        conn.commit()

        result = recover_workspace(conn, repo, "p1", ws_cfg, ticket_id="t1")

        # Teardown completed — no new workspace provisioned.
        assert result is None

        # The old workspace should be cleaned.
        old = conn.execute(
            "SELECT status FROM workspaces WHERE id = ?", (ws.workspace_id,)
        ).fetchone()
        assert old["status"] == "cleaned"

        # No new workspace was created.
        count = conn.execute(
            "SELECT COUNT(*) as c FROM workspaces WHERE ticket_id = 't1' "
            "AND status != 'cleaned'"
        ).fetchone()["c"]
        assert count == 0, (
            "No active workspace should exist after tearing_down recovery"
        )
