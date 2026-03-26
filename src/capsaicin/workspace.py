"""Git workspace manager for isolated execution environments.

Provisions, validates, reuses, and retires isolated git worktrees
without mutating the operator's active checkout.  All state transitions
are persisted in the ``workspaces`` table defined by migration 0012.
"""

from __future__ import annotations

import enum
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from capsaicin.config import Config, WorkspaceConfig
from capsaicin.queries import generate_id, now_utc


# ---------------------------------------------------------------------------
# Typed recovery outcomes (AC-2)
# ---------------------------------------------------------------------------


class RecoveryAction(enum.Enum):
    """Recommended action when workspace validation fails."""

    retry = "retry"
    recreate = "recreate"
    human_gate = "human_gate"


RECOVERY_MAP: dict[str, RecoveryAction] = {
    "dirty_base_repo": RecoveryAction.retry,
    "missing_worktree": RecoveryAction.recreate,
    "branch_drift": RecoveryAction.recreate,
    "setup_failure": RecoveryAction.retry,
    "cleanup_conflict": RecoveryAction.human_gate,
}


def get_recovery_action(failure_reason: str | None) -> RecoveryAction | None:
    """Look up the recommended recovery action for a failure reason.

    Returns ``None`` when *failure_reason* is ``None`` or not recognised.
    """
    if failure_reason is None:
        return None
    return RECOVERY_MAP.get(failure_reason)


@dataclass(frozen=True)
class WorkspaceRecovery:
    """Typed recovery outcome returned instead of silently proceeding."""

    workspace_id: str
    failure_reason: str
    action: RecoveryAction
    detail: str

    @staticmethod
    def for_reason(workspace_id: str, reason: str, detail: str) -> WorkspaceRecovery:
        action = RECOVERY_MAP.get(reason, RecoveryAction.human_gate)
        return WorkspaceRecovery(
            workspace_id=workspace_id,
            failure_reason=reason,
            action=action,
            detail=detail,
        )


@dataclass(frozen=True)
class WorkspaceReady:
    """Indicates the workspace is valid and ready for use."""

    workspace_id: str
    worktree_path: str
    branch_name: str


# Union-style result: callers check isinstance.
WorkspaceResult = WorkspaceReady | WorkspaceRecovery


class WorkspaceBlockedError(Exception):
    """Raised when workspace acquisition fails and the pipeline must stop."""

    def __init__(self, recovery: WorkspaceRecovery) -> None:
        self.recovery = recovery
        super().__init__(
            f"Workspace blocked ({recovery.failure_reason}): {recovery.detail}"
        )


# ---------------------------------------------------------------------------
# Setup-command result (AC-3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetupSuccess:
    """Setup commands completed successfully."""

    workspace_id: str


@dataclass(frozen=True)
class SetupFailure:
    """Setup command failed; detail persisted for retry or human-gate."""

    workspace_id: str
    command: str
    exit_code: int
    stderr: str


SetupResult = SetupSuccess | SetupFailure


# ---------------------------------------------------------------------------
# Internal git helpers
# ---------------------------------------------------------------------------


def _git(
    args: list[str], cwd: str | Path, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _working_tree_is_clean(repo_path: str | Path) -> bool:
    result = _git(["status", "--porcelain"], repo_path)
    return result.returncode == 0 and result.stdout.strip() == ""


def _resolve_ref(repo_path: str | Path, ref: str = "HEAD") -> str:
    """Return the full commit SHA for *ref*."""
    result = _git(["rev-parse", ref], repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse {ref} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _branch_exists(repo_path: str | Path, branch: str) -> bool:
    result = _git(["rev-parse", "--verify", f"refs/heads/{branch}"], repo_path)
    return result.returncode == 0


def _worktree_list(repo_path: str | Path) -> list[str]:
    """Return worktree paths registered with git."""
    result = _git(["worktree", "list", "--porcelain"], repo_path)
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(line[len("worktree ") :])
    return paths


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _insert_workspace(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    project_id: str,
    ticket_id: str | None,
    epic_id: str | None,
    worktree_path: str,
    branch_name: str,
    base_ref: str,
    status: str = "pending",
) -> None:
    now = now_utc()
    conn.execute(
        "INSERT INTO workspaces "
        "(id, project_id, ticket_id, epic_id, worktree_path, branch_name, "
        "base_ref, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            workspace_id,
            project_id,
            ticket_id,
            epic_id,
            worktree_path,
            branch_name,
            base_ref,
            status,
            now,
            now,
        ),
    )
    conn.commit()


def _update_status(
    conn: sqlite3.Connection,
    workspace_id: str,
    status: str,
    failure_reason: str | None = None,
    failure_detail: str | None = None,
) -> None:
    conn.execute(
        "UPDATE workspaces SET status = ?, failure_reason = ?, "
        "failure_detail = ?, updated_at = ? WHERE id = ?",
        (status, failure_reason, failure_detail, now_utc(), workspace_id),
    )
    conn.commit()


def _load_workspace(conn: sqlite3.Connection, workspace_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _find_active_workspace(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    ticket_id: str | None = None,
    epic_id: str | None = None,
) -> dict | None:
    """Find an existing non-terminal workspace for a ticket or epic."""
    if ticket_id is not None:
        row = conn.execute(
            "SELECT * FROM workspaces "
            "WHERE project_id = ? AND ticket_id = ? "
            "AND status NOT IN ('cleaned', 'failed') "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id, ticket_id),
        ).fetchone()
    elif epic_id is not None:
        row = conn.execute(
            "SELECT * FROM workspaces "
            "WHERE project_id = ? AND epic_id = ? "
            "AND status NOT IN ('cleaned', 'failed') "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id, epic_id),
        ).fetchone()
    else:
        raise ValueError("Either ticket_id or epic_id must be provided")
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_workspace(
    conn: sqlite3.Connection,
    repo_path: str | Path,
    project_id: str,
    ws_config: WorkspaceConfig,
    *,
    ticket_id: str | None = None,
    epic_id: str | None = None,
) -> WorkspaceResult:
    """Provision an isolated git worktree for a ticket or epic.

    Creates the worktree, persists the workspace row, and transitions
    through ``pending → setting_up → active``.  On failure the workspace
    is marked ``failed`` with the appropriate reason and a
    ``WorkspaceRecovery`` is returned.

    The operator's active checkout is never mutated.
    """
    if ticket_id is None and epic_id is None:
        raise ValueError("Either ticket_id or epic_id must be provided")
    if ticket_id is not None and epic_id is not None:
        raise ValueError("Only one of ticket_id or epic_id may be provided")

    repo = Path(repo_path).resolve()
    workspace_id = generate_id()

    # Derive branch and worktree path.
    slug = ticket_id or epic_id
    branch_name = f"{ws_config.branch_prefix}{slug}"
    worktree_path = str(repo / ".worktrees" / slug)

    # Resolve base ref before any mutation.
    base_ref = _resolve_ref(repo)

    # Persist the pending workspace row.
    _insert_workspace(
        conn,
        workspace_id=workspace_id,
        project_id=project_id,
        ticket_id=ticket_id,
        epic_id=epic_id,
        worktree_path=worktree_path,
        branch_name=branch_name,
        base_ref=base_ref,
    )

    # Pre-setup check: base repo must have a clean working tree.
    if not _working_tree_is_clean(repo):
        detail = "The base repository has uncommitted changes."
        _update_status(conn, workspace_id, "failed", "dirty_base_repo", detail)
        return WorkspaceRecovery.for_reason(workspace_id, "dirty_base_repo", detail)

    # Transition to setting_up.
    _update_status(conn, workspace_id, "setting_up")

    # Create the worktree with a new branch.
    result = _git(
        ["worktree", "add", "-b", branch_name, worktree_path, "HEAD"],
        repo,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Distinguish branch-drift (branch already exists) from other errors.
        if _branch_exists(repo, branch_name):
            reason = "branch_drift"
            detail = f"Branch '{branch_name}' already exists and may have diverged."
        else:
            reason = "setup_failure"
            detail = f"git worktree add failed (exit {result.returncode}): {stderr}"
        _update_status(conn, workspace_id, "failed", reason, detail)
        return WorkspaceRecovery.for_reason(workspace_id, reason, detail)

    # Transition to active.
    _update_status(conn, workspace_id, "active")

    return WorkspaceReady(
        workspace_id=workspace_id,
        worktree_path=worktree_path,
        branch_name=branch_name,
    )


def validate_workspace(
    conn: sqlite3.Connection,
    repo_path: str | Path,
    workspace_id: str,
) -> WorkspaceResult:
    """Validate that recorded workspace metadata matches actual git state.

    Returns ``WorkspaceReady`` when everything is consistent, or a typed
    ``WorkspaceRecovery`` describing the mismatch and recommended action.
    """
    ws = _load_workspace(conn, workspace_id)
    if ws is None:
        raise KeyError(f"No workspace found for id={workspace_id!r}")

    # Already failed — surface the existing recovery.
    if ws["status"] == "failed":
        return WorkspaceRecovery.for_reason(
            workspace_id,
            ws["failure_reason"],
            ws.get("failure_detail") or "Workspace previously failed.",
        )

    repo = Path(repo_path).resolve()
    wt_path = Path(ws["worktree_path"])

    # Check worktree exists on disk.
    if not wt_path.is_dir():
        detail = f"Worktree path does not exist: {wt_path}"
        _update_status(conn, workspace_id, "failed", "missing_worktree", detail)
        return WorkspaceRecovery.for_reason(workspace_id, "missing_worktree", detail)

    # Check worktree is still registered with git.
    registered = _worktree_list(repo)
    if str(wt_path) not in registered:
        detail = f"Worktree at {wt_path} is not registered with git."
        _update_status(conn, workspace_id, "failed", "missing_worktree", detail)
        return WorkspaceRecovery.for_reason(workspace_id, "missing_worktree", detail)

    # Check branch hasn't drifted from recorded base_ref.
    current_base = _resolve_ref(repo, "HEAD")
    if current_base != ws["base_ref"]:
        detail = (
            f"Base branch has moved: recorded {ws['base_ref'][:12]}, "
            f"current {current_base[:12]}."
        )
        _update_status(conn, workspace_id, "failed", "branch_drift", detail)
        return WorkspaceRecovery.for_reason(workspace_id, "branch_drift", detail)

    return WorkspaceReady(
        workspace_id=workspace_id,
        worktree_path=ws["worktree_path"],
        branch_name=ws["branch_name"],
    )


def acquire_workspace(
    conn: sqlite3.Connection,
    repo_path: str | Path,
    project_id: str,
    ws_config: WorkspaceConfig,
    *,
    ticket_id: str | None = None,
    epic_id: str | None = None,
) -> WorkspaceResult:
    """Reuse an existing active workspace or create a new one.

    Looks for a non-terminal workspace for the given ticket/epic.  If
    one exists, validates it and returns the result.  Otherwise creates
    a fresh workspace.
    """
    existing = _find_active_workspace(
        conn, project_id, ticket_id=ticket_id, epic_id=epic_id
    )
    if existing is not None:
        return validate_workspace(conn, repo_path, existing["id"])

    return create_workspace(
        conn,
        repo_path,
        project_id,
        ws_config,
        ticket_id=ticket_id,
        epic_id=epic_id,
    )


def resolve_execution_path(
    conn: sqlite3.Connection,
    config: Config,
    *,
    ticket_id: str | None = None,
    epic_id: str | None = None,
) -> str:
    """Return the effective working directory for a pipeline run.

    When workspace isolation is enabled (``config.workspace.enabled``),
    acquires or validates an isolated worktree and returns its path.
    When disabled, returns ``config.project.repo_path`` unchanged.

    Raises ``WorkspaceBlockedError`` if the workspace cannot be acquired
    (missing, stale, or diverged), giving the caller a deterministic
    signal to stop the pipeline with a blocked or human-gate outcome.
    """
    if not config.workspace.enabled:
        return config.project.repo_path

    # Look up the project_id from the DB.
    row = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
    project_id = row["id"]

    result = acquire_workspace(
        conn,
        config.project.repo_path,
        project_id,
        config.workspace,
        ticket_id=ticket_id,
        epic_id=epic_id,
    )
    if isinstance(result, WorkspaceRecovery):
        raise WorkspaceBlockedError(result)

    return result.worktree_path


def resolve_or_block(
    conn: sqlite3.Connection,
    config: Config,
    ticket_id: str,
    log_path: str | Path | None = None,
) -> str | None:
    """Resolve the execution path, blocking the ticket on workspace failure.

    Convenience wrapper around :func:`resolve_execution_path` that catches
    ``WorkspaceBlockedError`` and transitions the ticket to ``blocked``
    with the appropriate reason.

    Returns the working directory string on success, or ``None`` if the
    ticket was blocked.  When ``None`` is returned the caller is
    responsible for any remaining orchestrator cleanup (e.g.
    ``finish_run`` / ``set_idle``) before returning ``"blocked"``.
    """
    from capsaicin.state_machine import transition_ticket

    try:
        return resolve_execution_path(conn, config, ticket_id=ticket_id)
    except WorkspaceBlockedError as exc:
        blocked_reason = "workspace_" + exc.recovery.failure_reason
        transition_ticket(
            conn,
            ticket_id,
            "blocked",
            "system",
            reason=f"Workspace unavailable: {exc.recovery.detail}",
            blocked_reason=blocked_reason,
            log_path=log_path,
        )
        return None


def get_workspace_info(
    conn: sqlite3.Connection,
    *,
    ticket_id: str | None = None,
    epic_id: str | None = None,
    workspace_id: str | None = None,
) -> dict | None:
    """Return workspace metadata for display/inspection.

    Looks up by workspace_id directly, or finds the most recent
    non-terminal workspace for a ticket/epic.  Returns ``None`` when
    no matching workspace exists.
    """
    if workspace_id is not None:
        return _load_workspace(conn, workspace_id)

    if ticket_id is None and epic_id is None:
        raise ValueError("Provide ticket_id, epic_id, or workspace_id")

    # Find most recent workspace (any status) for the entity.
    if ticket_id is not None:
        row = conn.execute(
            "SELECT * FROM workspaces "
            "WHERE ticket_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM workspaces "
            "WHERE epic_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (epic_id,),
        ).fetchone()

    return dict(row) if row else None


def cleanup_workspace(
    conn: sqlite3.Connection,
    repo_path: str | Path,
    workspace_id: str,
    ws_config: WorkspaceConfig,
) -> WorkspaceRecovery | None:
    """Tear down an isolated workspace: remove worktree, optionally delete branch.

    Transitions the workspace through ``tearing_down → cleaned``.
    Returns ``None`` on success, or a ``WorkspaceRecovery`` with
    ``cleanup_conflict`` if git operations fail.

    Enforces the same safety checks as automated pipeline use:
    the workspace must exist and not already be in a terminal state
    (``cleaned``).
    """
    ws = _load_workspace(conn, workspace_id)
    if ws is None:
        raise KeyError(f"No workspace found for id={workspace_id!r}")

    if ws["status"] == "cleaned":
        return None  # Already cleaned — idempotent.

    repo = Path(repo_path).resolve()
    wt_path = ws["worktree_path"]
    branch_name = ws["branch_name"]

    # Transition to tearing_down.
    _update_status(conn, workspace_id, "tearing_down")

    # Remove the worktree if it still exists.
    if Path(wt_path).is_dir():
        # Safety check: refuse to force-remove a worktree with uncommitted
        # changes — surface cleanup_conflict so the operator can decide.
        if not _working_tree_is_clean(wt_path):
            detail = (
                f"Worktree {wt_path} has uncommitted changes. "
                "Commit or discard changes before cleanup."
            )
            _update_status(conn, workspace_id, "failed", "cleanup_conflict", detail)
            return WorkspaceRecovery.for_reason(
                workspace_id, "cleanup_conflict", detail
            )

        result = _git(["worktree", "remove", wt_path], repo)
        if result.returncode != 0:
            detail = (
                f"git worktree remove failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
            _update_status(conn, workspace_id, "failed", "cleanup_conflict", detail)
            return WorkspaceRecovery.for_reason(
                workspace_id, "cleanup_conflict", detail
            )

    # Prune stale worktree references.
    _git(["worktree", "prune"], repo)

    # Delete the branch if auto_cleanup is enabled and branch exists.
    if ws_config.auto_cleanup and _branch_exists(repo, branch_name):
        result = _git(["branch", "-D", branch_name], repo)
        if result.returncode != 0:
            detail = (
                f"git branch -D failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
            _update_status(conn, workspace_id, "failed", "cleanup_conflict", detail)
            return WorkspaceRecovery.for_reason(
                workspace_id, "cleanup_conflict", detail
            )

    _update_status(conn, workspace_id, "cleaned")
    return None


def recover_workspace(
    conn: sqlite3.Connection,
    repo_path: str | Path,
    project_id: str,
    ws_config: WorkspaceConfig,
    *,
    ticket_id: str | None = None,
    epic_id: str | None = None,
) -> WorkspaceResult:
    """Attempt to recover a failed workspace.

    Cleans up the failed workspace (if one exists) and creates a fresh
    one.  Enforces the same safety checks as ``create_workspace``.
    """
    existing = _find_active_workspace(
        conn, project_id, ticket_id=ticket_id, epic_id=epic_id
    )

    # If an active workspace exists, validate it first — a healthy
    # workspace should be preserved, matching acquire_workspace().
    if existing is not None:
        validation = validate_workspace(conn, repo_path, existing["id"])
        if isinstance(validation, WorkspaceReady):
            return validation
        # Validation failed — clean up before recreating.
        recovery = cleanup_workspace(conn, repo_path, existing["id"], ws_config)
        if recovery is not None:
            return recovery
    else:
        # No active workspace — look for failed ones that need cleanup.
        if ticket_id is not None:
            row = conn.execute(
                "SELECT * FROM workspaces "
                "WHERE project_id = ? AND ticket_id = ? AND status = 'failed' "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id, ticket_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM workspaces "
                "WHERE project_id = ? AND epic_id = ? AND status = 'failed' "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id, epic_id),
            ).fetchone()
        failed = dict(row) if row else None
        if failed is not None:
            recovery = cleanup_workspace(conn, repo_path, failed["id"], ws_config)
            if recovery is not None:
                return recovery

    return create_workspace(
        conn,
        repo_path,
        project_id,
        ws_config,
        ticket_id=ticket_id,
        epic_id=epic_id,
    )


def run_setup_commands(
    conn: sqlite3.Connection,
    workspace_id: str,
    commands: list[str],
    *,
    timeout: int = 120,
) -> SetupResult:
    """Execute setup commands inside the isolated workspace.

    Commands run sequentially in the worktree directory.  On first
    failure, the workspace is transitioned to ``failed`` with
    ``setup_failure`` and enough detail is persisted for retry or
    human-gate decisions.
    """
    ws = _load_workspace(conn, workspace_id)
    if ws is None:
        raise KeyError(f"No workspace found for id={workspace_id!r}")

    wt_path = ws["worktree_path"]

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=wt_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            detail = f"Setup command timed out after {timeout}s: {cmd}"
            _update_status(conn, workspace_id, "failed", "setup_failure", detail)
            return SetupFailure(
                workspace_id=workspace_id,
                command=cmd,
                exit_code=-1,
                stderr=detail,
            )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            detail = (
                f"Setup command failed (exit {result.returncode}): {cmd}\n"
                f"stderr: {stderr}"
            )
            _update_status(conn, workspace_id, "failed", "setup_failure", detail)
            return SetupFailure(
                workspace_id=workspace_id,
                command=cmd,
                exit_code=result.returncode,
                stderr=stderr,
            )

    return SetupSuccess(workspace_id=workspace_id)
