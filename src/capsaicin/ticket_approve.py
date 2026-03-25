"""Approval pipeline for ``capsaicin ticket approve`` (T21).

Human approval gate with workspace verification, divergence persistence,
and approval-time metadata capture for downstream commit/PR workflows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.diff import capture_diff, capture_git_metadata, diffs_match, get_run_diff
from capsaicin.orchestrator import set_idle
from capsaicin.queries import generate_id, now_utc
from capsaicin.state_machine import transition_ticket


# Gate reasons that require a rationale for approval.
_RATIONALE_REQUIRED_GATES = frozenset(
    {"cycle_limit", "reviewer_escalated", "low_confidence_pass"}
)


class WorkspaceMismatchError(Exception):
    """Raised when workspace does not match the reviewed diff."""


# ---------------------------------------------------------------------------
# Workspace check detail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _WorkspaceCheckDetail:
    """Internal result of a detailed workspace comparison."""

    matches: bool
    expected_diff: str | None = None
    actual_diff: str | None = None
    divergence_type: str | None = None
    workspace_id: str | None = None


# ---------------------------------------------------------------------------
# Ticket selection
# ---------------------------------------------------------------------------


def select_approve_ticket(
    conn: sqlite3.Connection, ticket_id: str | None = None
) -> dict:
    """Select a ticket for approval.

    If *ticket_id* is given, validate that it exists and is in ``human-gate``.
    Otherwise auto-select the first ``human-gate`` ticket ordered by
    ``status_changed_at``.

    Returns a dict with ticket row data.
    Raises ``ValueError`` if no eligible ticket is found.
    """
    from capsaicin.queries import TICKET_COLUMNS, load_ticket

    from capsaicin.errors import InvalidStatusError, NoEligibleTicketError

    if ticket_id:
        ticket = load_ticket(conn, ticket_id)
        if ticket["status"] != "human-gate":
            raise InvalidStatusError(ticket_id, ticket["status"], "human-gate")
        return ticket

    row = conn.execute(
        f"SELECT {TICKET_COLUMNS} "
        "FROM tickets WHERE status = 'human-gate' "
        "ORDER BY status_changed_at"
    ).fetchone()

    if row is None:
        raise NoEligibleTicketError("No ticket found in 'human-gate' status.")

    return dict(row)


# ---------------------------------------------------------------------------
# Workspace verification
# ---------------------------------------------------------------------------


def _check_workspace_detailed(
    conn: sqlite3.Connection, repo_path: str | Path, ticket_id: str
) -> _WorkspaceCheckDetail:
    """Compare current workspace against the reviewed diff, returning details.

    Returns a ``_WorkspaceCheckDetail`` with match status, expected/actual
    diffs, and divergence type when a mismatch is found.
    """
    # Prefer the review baseline from the latest successful reviewer run
    row = conn.execute(
        "SELECT rb.baseline_diff "
        "FROM review_baselines rb "
        "JOIN agent_runs ar ON ar.id = rb.run_id "
        "WHERE ar.ticket_id = ? AND ar.role = 'reviewer' "
        "AND ar.exit_status = 'success' "
        "ORDER BY ar.started_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()

    if row is not None:
        current = capture_diff(repo_path)
        if diffs_match(row["baseline_diff"], current.diff_text):
            return _WorkspaceCheckDetail(matches=True)
        return _WorkspaceCheckDetail(
            matches=False,
            expected_diff=row["baseline_diff"],
            actual_diff=current.diff_text,
            divergence_type="diff_mismatch",
        )

    # Fallback: compare against the implementer run diff
    impl_row = conn.execute(
        "SELECT id FROM agent_runs "
        "WHERE ticket_id = ? AND role = 'implementer' "
        "ORDER BY started_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if impl_row is None:
        return _WorkspaceCheckDetail(matches=True)

    try:
        stored = get_run_diff(conn, impl_row["id"])
    except KeyError:
        return _WorkspaceCheckDetail(matches=True)

    current = capture_diff(repo_path)
    if diffs_match(stored.diff_text, current.diff_text):
        return _WorkspaceCheckDetail(matches=True)
    return _WorkspaceCheckDetail(
        matches=False,
        expected_diff=stored.diff_text,
        actual_diff=current.diff_text,
        divergence_type="diff_mismatch",
    )


def check_workspace_matches(
    conn: sqlite3.Connection, repo_path: str | Path, ticket_id: str
) -> bool:
    """Check whether the current workspace matches the diff that was reviewed.

    Compares against the review baseline of the most recent successful
    reviewer run, which captures the workspace state at the time the
    reviewer was invoked.  Falls back to the implementer run diff if
    no review baseline exists.

    Returns True if the workspace matches (or no baseline exists).
    """
    return _check_workspace_detailed(conn, repo_path, ticket_id).matches


def _fetch_workspace_row(conn: sqlite3.Connection, ticket_id: str) -> dict | None:
    """Fetch the most recent usable workspace row for *ticket_id*.

    Returns ``None`` when no workspace exists (excluding cleaned/failed).
    The result is shared by ``_resolve_workspace_info`` and
    ``_check_workspace_with_isolation`` so both operate on the same snapshot.
    """
    return conn.execute(
        "SELECT id, worktree_path, branch_name, status "
        "FROM workspaces WHERE ticket_id = ? "
        "AND status NOT IN ('cleaned', 'failed') "
        "ORDER BY created_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()


def _resolve_workspace_info(
    config, ws_row: dict | None
) -> tuple[str, str | None, str | None]:
    """Resolve the effective path, workspace_id, and branch for approval.

    When workspace isolation is enabled and an active workspace exists,
    validates the workspace and returns its worktree path.  Otherwise
    returns the base repo_path.

    *ws_row* is the result of ``_fetch_workspace_row`` (may be ``None``).

    Returns (effective_path, workspace_id_or_None, branch_name_or_None).
    """
    if not config.workspace.enabled:
        return config.project.repo_path, None, None

    if ws_row is None:
        return config.project.repo_path, None, None

    if ws_row["status"] == "active" and Path(ws_row["worktree_path"]).is_dir():
        return ws_row["worktree_path"], ws_row["id"], ws_row["branch_name"]

    # Workspace exists but is not usable — treated as workspace_invalid
    return config.project.repo_path, ws_row["id"], ws_row["branch_name"]


def _check_workspace_with_isolation(
    conn: sqlite3.Connection,
    config,
    ticket_id: str,
    ws_row: dict | None,
) -> _WorkspaceCheckDetail:
    """Extended workspace check that also validates isolated workspace state.

    When workspace isolation is enabled, checks that the workspace is still
    active and usable before comparing diffs.  A non-active workspace
    produces a ``workspace_invalid`` divergence.

    *ws_row* is the result of ``_fetch_workspace_row`` (may be ``None``).
    """
    if not config.workspace.enabled:
        return _check_workspace_detailed(conn, config.project.repo_path, ticket_id)

    if ws_row is None:
        # No workspace — fall back to base repo diff check
        return _check_workspace_detailed(conn, config.project.repo_path, ticket_id)

    if ws_row["status"] != "active" or not Path(ws_row["worktree_path"]).is_dir():
        return _WorkspaceCheckDetail(
            matches=False,
            divergence_type="workspace_invalid",
            workspace_id=ws_row["id"],
        )

    detail = _check_workspace_detailed(conn, ws_row["worktree_path"], ticket_id)
    return replace(detail, workspace_id=ws_row["id"])


# ---------------------------------------------------------------------------
# Divergence persistence (AC-2)
# ---------------------------------------------------------------------------


def _persist_divergence(
    conn: sqlite3.Connection,
    ticket_id: str,
    detail: _WorkspaceCheckDetail,
    recovery_action: str,
) -> str:
    """Insert a workspace divergence record and return its id."""
    div_id = generate_id()
    conn.execute(
        "INSERT INTO workspace_divergences "
        "(id, ticket_id, workspace_id, expected_diff, actual_diff, "
        "divergence_type, recovery_action, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            div_id,
            ticket_id,
            detail.workspace_id,
            detail.expected_diff,
            detail.actual_diff,
            detail.divergence_type,
            recovery_action,
            now_utc(),
        ),
    )
    conn.commit()
    return div_id


# ---------------------------------------------------------------------------
# Approval metadata (AC-3)
# ---------------------------------------------------------------------------


def _persist_approval_metadata(
    conn: sqlite3.Connection,
    decision_id: str,
    workspace_id: str | None,
    branch_name: str,
    worktree_path: str,
    commit_ref: str,
) -> None:
    """Capture git metadata at approval time for downstream workflows."""
    conn.execute(
        "INSERT INTO approval_metadata "
        "(decision_id, workspace_id, branch_name, worktree_path, "
        "commit_ref, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (decision_id, workspace_id, branch_name, worktree_path, commit_ref, now_utc()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def approve_ticket(
    conn: sqlite3.Connection,
    project_id: str,
    ticket: dict,
    repo_path: str | Path,
    rationale: str | None = None,
    force: bool = False,
    log_path: str | Path | None = None,
    config=None,
) -> str:
    """Execute the approval pipeline for a ticket.

    When *config* is provided and workspace isolation is enabled, the
    workspace is validated before comparing diffs.  Divergence events are
    persisted regardless of whether *force* overrides the check.

    Returns the final ticket status ('pr-ready').

    Raises:
        WorkspaceMismatchError: if workspace doesn't match and force is False.
        ValueError: if rationale is required but not provided.
    """
    ticket_id = ticket["id"]
    gate_reason = ticket.get("gate_reason")

    # --- Workspace verification (AC-1 / AC-2) ---
    if config is not None:
        ws_row = _fetch_workspace_row(conn, ticket_id)
        detail = _check_workspace_with_isolation(conn, config, ticket_id, ws_row)
        effective_path, ws_id, ws_branch = _resolve_workspace_info(config, ws_row)
    else:
        detail = _check_workspace_detailed(conn, repo_path, ticket_id)
        effective_path = str(repo_path)
        ws_id = None
        ws_branch = None

    if not detail.matches:
        recovery_action = "force_override" if force else "rejected"
        div_id = _persist_divergence(conn, ticket_id, detail, recovery_action)

        if log_path:
            log_event(
                log_path,
                "WORKSPACE_DIVERGENCE",
                project_id=project_id,
                ticket_id=ticket_id,
                payload={
                    "divergence_id": div_id,
                    "divergence_type": detail.divergence_type,
                    "recovery_action": recovery_action,
                    "workspace_id": detail.workspace_id or ws_id,
                },
            )

        if not force:
            raise WorkspaceMismatchError(
                "Workspace does not match the reviewed implementation diff. "
                "Use --force to override."
            )

    # --- Rationale check ---
    if gate_reason in _RATIONALE_REQUIRED_GATES and not rationale:
        raise ValueError(
            f"Rationale is required when gate_reason is '{gate_reason}'. "
            "Use --rationale to provide one."
        )

    # --- Record decision ---
    decision_id = generate_id()
    conn.execute(
        "INSERT INTO decisions (id, ticket_id, decision, rationale, created_at) "
        "VALUES (?, ?, 'approve', ?, ?)",
        (decision_id, ticket_id, rationale, now_utc()),
    )
    conn.commit()

    # --- Capture approval metadata (AC-3) ---
    git_meta = capture_git_metadata(effective_path)
    _persist_approval_metadata(
        conn,
        decision_id=decision_id,
        workspace_id=ws_id or detail.workspace_id,
        branch_name=ws_branch or git_meta.branch_name,
        worktree_path=effective_path,
        commit_ref=git_meta.commit_ref,
    )

    # --- Transition to pr-ready ---
    transition_ticket(
        conn,
        ticket_id,
        "pr-ready",
        "human",
        reason=rationale or "Approved.",
        log_path=log_path,
    )

    # --- Set orchestrator to idle ---
    set_idle(conn, project_id)

    if log_path:
        log_event(
            log_path,
            "DECISION",
            project_id=project_id,
            ticket_id=ticket_id,
            payload={
                "decision": "approve",
                "gate_reason": gate_reason,
                "rationale": rationale,
            },
        )

    return "pr-ready"


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def build_approval_summary(conn: sqlite3.Connection, ticket_id: str) -> str:
    """Build a PR preparation summary for stdout."""
    ticket = conn.execute(
        "SELECT title, status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()

    criteria = conn.execute(
        "SELECT description, status FROM acceptance_criteria WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchall()

    open_findings = conn.execute(
        "SELECT COUNT(*) FROM findings WHERE ticket_id = ? AND disposition = 'open'",
        (ticket_id,),
    ).fetchone()[0]

    fixed_findings = conn.execute(
        "SELECT COUNT(*) FROM findings WHERE ticket_id = ? AND disposition = 'fixed'",
        (ticket_id,),
    ).fetchone()[0]

    lines = [
        f"Ticket: {ticket_id}",
        f"  Title: {ticket['title']}",
        f"  Status: {ticket['status']}",
        "",
        "Acceptance Criteria:",
    ]

    if criteria:
        for c in criteria:
            lines.append(f"  [{c['status']}] {c['description']}")
    else:
        lines.append("  (none)")

    lines.extend(
        [
            "",
            "Findings:",
            f"  Open: {open_findings}",
            f"  Fixed: {fixed_findings}",
        ]
    )

    return "\n".join(lines)
