"""Approval pipeline for ``capsaicin ticket approve`` (T21).

Human approval gate with workspace verification.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.diff import capture_diff, diffs_match, get_run_diff
from capsaicin.orchestrator import set_idle
from capsaicin.state_machine import transition_ticket


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id() -> str:
    from ulid import ULID

    return str(ULID())


# Gate reasons that require a rationale for approval.
_RATIONALE_REQUIRED_GATES = frozenset(
    {"cycle_limit", "reviewer_escalated", "low_confidence_pass"}
)


class WorkspaceMismatchError(Exception):
    """Raised when workspace does not match the reviewed diff."""


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
    if ticket_id:
        row = conn.execute(
            "SELECT id, project_id, title, description, status, gate_reason "
            "FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Ticket '{ticket_id}' not found.")
        if row["status"] != "human-gate":
            raise ValueError(
                f"Ticket '{ticket_id}' is in '{row['status']}' status; "
                "expected 'human-gate'."
            )
        return dict(row)

    row = conn.execute(
        "SELECT id, project_id, title, description, status, gate_reason "
        "FROM tickets WHERE status = 'human-gate' "
        "ORDER BY status_changed_at"
    ).fetchone()

    if row is None:
        raise ValueError("No ticket found in 'human-gate' status.")

    return dict(row)


# ---------------------------------------------------------------------------
# Workspace verification
# ---------------------------------------------------------------------------


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
        return diffs_match(row["baseline_diff"], current.diff_text)

    # Fallback: compare against the implementer run diff
    impl_row = conn.execute(
        "SELECT id FROM agent_runs "
        "WHERE ticket_id = ? AND role = 'implementer' "
        "ORDER BY started_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if impl_row is None:
        return True

    try:
        stored = get_run_diff(conn, impl_row["id"])
    except KeyError:
        return True

    current = capture_diff(repo_path)
    return diffs_match(stored.diff_text, current.diff_text)


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
) -> str:
    """Execute the approval pipeline for a ticket.

    Returns the final ticket status ('pr-ready').

    Raises:
        WorkspaceMismatchError: if workspace doesn't match and force is False.
        ValueError: if rationale is required but not provided.
    """
    ticket_id = ticket["id"]
    gate_reason = ticket.get("gate_reason")

    # --- Workspace verification ---
    if not force:
        if not check_workspace_matches(conn, repo_path, ticket_id):
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
    decision_id = _generate_id()
    conn.execute(
        "INSERT INTO decisions (id, ticket_id, decision, rationale, created_at) "
        "VALUES (?, ?, 'approve', ?, ?)",
        (decision_id, ticket_id, rationale, _now()),
    )
    conn.commit()

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
