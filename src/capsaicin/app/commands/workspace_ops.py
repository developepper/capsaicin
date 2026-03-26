"""Command services for workspace lifecycle operations.

Provides inspect, recover, and cleanup entry points that enforce
the same safety checks as automated pipeline use.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from capsaicin.config import Config


@dataclass
class WorkspaceStatusResult:
    """Structured outcome of a workspace status query."""

    ticket_id: str
    isolation_mode: str  # "shared", "branch", "worktree"
    workspace_id: str | None = None
    status: str | None = None
    branch_name: str | None = None
    worktree_path: str | None = None
    base_ref: str | None = None
    failure_reason: str | None = None
    failure_detail: str | None = None
    blocked_reason: str | None = None


@dataclass
class WorkspaceActionResult:
    """Structured outcome of a workspace recover or cleanup action."""

    ticket_id: str
    action: str  # "recovered", "cleaned", "failed"
    detail: str | None = None
    workspace_id: str | None = None


def workspace_status(
    conn: sqlite3.Connection,
    config: Config,
    ticket_id: str,
) -> WorkspaceStatusResult:
    """Inspect workspace isolation state for a ticket.

    Returns the isolation mode, workspace metadata, and any blocking
    reason.  Works regardless of whether isolation is enabled.
    """
    from capsaicin.workspace import (
        WorkspaceRecovery,
        branch_exists,
        worktree_list,
        get_workspace_info,
        validate_workspace,
    )

    # Check if ticket exists.
    ticket = conn.execute(
        "SELECT id, status, blocked_reason FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if ticket is None:
        raise ValueError(f"Ticket not found: {ticket_id}")

    if not config.workspace.enabled:
        return WorkspaceStatusResult(
            ticket_id=ticket_id,
            isolation_mode="shared",
            blocked_reason=ticket["blocked_reason"],
        )

    ws = get_workspace_info(conn, ticket_id=ticket_id)
    if ws is None:
        return WorkspaceStatusResult(
            ticket_id=ticket_id,
            isolation_mode="none",
            blocked_reason=ticket["blocked_reason"],
        )

    # Determine isolation mode from actual on-disk state, matching the
    # checks in validate_workspace(): a worktree is only reported as
    # active if the directory exists AND git still lists it as registered.
    repo_path = config.project.repo_path
    wt_path = ws.get("worktree_path")
    branch_name = ws.get("branch_name")
    registered = worktree_list(repo_path)
    if wt_path and Path(wt_path).is_dir() and str(Path(wt_path)) in registered:
        mode = "worktree"
    elif branch_name and branch_exists(repo_path, branch_name):
        mode = "branch"
    else:
        mode = "none"

    # Run the same validation path that automated execution uses so
    # that operators see the exact failure/block reason that would
    # prevent a pipeline run (e.g. missing_worktree, branch_drift).
    failure_reason = ws.get("failure_reason")
    failure_detail = ws.get("failure_detail")
    blocked_reason = ticket["blocked_reason"]

    ws_status = ws["status"]
    if ws_status not in ("cleaned", "failed"):
        validation = validate_workspace(conn, repo_path, ws["id"])
        if isinstance(validation, WorkspaceRecovery):
            failure_reason = validation.failure_reason
            failure_detail = validation.detail
            ws_status = "failed"
            if not blocked_reason:
                blocked_reason = f"workspace_{validation.failure_reason}"

    return WorkspaceStatusResult(
        ticket_id=ticket_id,
        isolation_mode=mode,
        workspace_id=ws["id"],
        status=ws_status,
        branch_name=ws.get("branch_name"),
        worktree_path=ws.get("worktree_path"),
        base_ref=ws.get("base_ref"),
        failure_reason=failure_reason,
        failure_detail=failure_detail,
        blocked_reason=blocked_reason,
    )


def workspace_recover(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    ticket_id: str,
) -> WorkspaceActionResult:
    """Recover a failed or missing workspace for a ticket.

    Cleans up any existing failed workspace and creates a fresh one.
    Enforces the same safety checks as automated pipeline use.
    """
    from capsaicin.workspace import (
        WorkspaceReady,
        WorkspaceRecovery,
        recover_workspace,
    )

    # Verify ticket exists.
    ticket = conn.execute(
        "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if ticket is None:
        raise ValueError(f"Ticket not found: {ticket_id}")

    if not config.workspace.enabled:
        raise ValueError(
            "Workspace isolation is not enabled. "
            "Set [workspace] enabled = true in config.toml."
        )

    result = recover_workspace(
        conn,
        config.project.repo_path,
        project_id,
        config.workspace,
        ticket_id=ticket_id,
    )

    if result is None:
        return WorkspaceActionResult(
            ticket_id=ticket_id,
            action="recovered",
            detail="Interrupted teardown completed. No workspace provisioned.",
        )

    if isinstance(result, WorkspaceReady):
        return WorkspaceActionResult(
            ticket_id=ticket_id,
            action="recovered",
            detail=f"Workspace ready: branch {result.branch_name} at {result.worktree_path}",
            workspace_id=result.workspace_id,
        )

    assert isinstance(result, WorkspaceRecovery)
    return WorkspaceActionResult(
        ticket_id=ticket_id,
        action="failed",
        detail=f"Recovery failed ({result.failure_reason}): {result.detail}",
        workspace_id=result.workspace_id,
    )


def workspace_cleanup(
    conn: sqlite3.Connection,
    config: Config,
    ticket_id: str,
) -> WorkspaceActionResult:
    """Clean up the workspace for a ticket.

    Removes the worktree and optionally deletes the branch.
    Enforces the same safety checks as automated pipeline use.
    """
    from capsaicin.workspace import (
        WorkspaceRecovery,
        cleanup_workspace,
        get_workspace_info,
    )

    # Verify ticket exists.
    ticket = conn.execute(
        "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if ticket is None:
        raise ValueError(f"Ticket not found: {ticket_id}")

    if not config.workspace.enabled:
        raise ValueError(
            "Workspace isolation is not enabled. "
            "Set [workspace] enabled = true in config.toml."
        )

    ws = get_workspace_info(conn, ticket_id=ticket_id)
    if ws is None:
        return WorkspaceActionResult(
            ticket_id=ticket_id,
            action="cleaned",
            detail="No workspace found for this ticket.",
        )

    if ws["status"] == "cleaned":
        return WorkspaceActionResult(
            ticket_id=ticket_id,
            action="cleaned",
            detail="Workspace already cleaned.",
            workspace_id=ws["id"],
        )

    recovery = cleanup_workspace(
        conn, config.project.repo_path, ws["id"], config.workspace
    )

    if isinstance(recovery, WorkspaceRecovery):
        return WorkspaceActionResult(
            ticket_id=ticket_id,
            action="failed",
            detail=f"Cleanup failed ({recovery.failure_reason}): {recovery.detail}",
            workspace_id=ws["id"],
        )

    return WorkspaceActionResult(
        ticket_id=ticket_id,
        action="cleaned",
        detail=f"Workspace {ws['id']} cleaned successfully.",
        workspace_id=ws["id"],
    )
