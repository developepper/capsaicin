"""State machine module for ticket transitions (T08).

Enforces ticket transition rules from state-machine.md as a reusable module.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from capsaicin.activity_log import log_event


# Valid ticket statuses
STATUSES = frozenset(
    {
        "ready",
        "implementing",
        "in-review",
        "revise",
        "human-gate",
        "pr-ready",
        "blocked",
        "done",
    }
)

# Valid actors
ACTORS = frozenset({"system", "implementer", "reviewer", "human"})

# Legal transitions: (from_status, to_status) -> set of allowed actors
LEGAL_TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    ("ready", "implementing"): frozenset({"system"}),
    ("implementing", "in-review"): frozenset({"system"}),
    ("implementing", "human-gate"): frozenset({"system"}),
    ("implementing", "blocked"): frozenset({"system"}),
    ("in-review", "revise"): frozenset({"system"}),
    ("in-review", "human-gate"): frozenset({"system"}),
    ("in-review", "blocked"): frozenset({"system"}),
    ("revise", "implementing"): frozenset({"system"}),
    ("revise", "human-gate"): frozenset({"system"}),
    ("human-gate", "pr-ready"): frozenset({"human"}),
    ("human-gate", "revise"): frozenset({"human"}),
    ("human-gate", "blocked"): frozenset({"human"}),
    ("pr-ready", "done"): frozenset({"system", "human"}),
    ("blocked", "ready"): frozenset({"human"}),
    ("blocked", "done"): frozenset({"human"}),
}


def transition_is_legal(from_status: str, to_status: str, actor: str) -> bool:
    """Check whether a transition is allowed for the given actor."""
    allowed = LEGAL_TRANSITIONS.get((from_status, to_status))
    if allowed is None:
        return False
    return actor in allowed


def _check_dependencies_satisfied(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """Return True if all dependencies of ticket_id are in 'done' status."""
    rows = conn.execute(
        "SELECT t.status FROM ticket_dependencies td "
        "JOIN tickets t ON t.id = td.depends_on_id "
        "WHERE td.ticket_id = ?",
        (ticket_id,),
    ).fetchall()
    return all(row[0] == "done" for row in rows)


class IllegalTransitionError(Exception):
    """Raised when a ticket transition is not allowed."""


class DependenciesNotSatisfiedError(Exception):
    """Raised when ready -> implementing is attempted with unmet dependencies."""


def transition_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    to_status: str,
    triggered_by: str,
    reason: str | None = None,
    gate_reason: str | None = None,
    blocked_reason: str | None = None,
    log_path: str | Path | None = None,
) -> None:
    """Transition a ticket to a new status.

    Validates the transition is legal, updates the ticket row, and records
    a state_transitions row. Optionally logs to activity.log.

    Args:
        conn: Database connection.
        ticket_id: The ticket to transition.
        to_status: Target status.
        triggered_by: The actor triggering the transition.
        reason: Optional human-readable reason.
        gate_reason: Set on tickets entering human-gate.
        blocked_reason: Set on tickets entering blocked.
        log_path: Path to activity.log for event logging.
    """
    row = conn.execute(
        "SELECT status, project_id FROM tickets t "
        "JOIN projects p ON p.id = t.project_id "
        "WHERE t.id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")

    from_status = row[0]
    project_id = row[1]

    if not transition_is_legal(from_status, to_status, triggered_by):
        raise IllegalTransitionError(
            f"Transition '{from_status}' -> '{to_status}' by '{triggered_by}' is not allowed."
        )

    # Guard: ready -> implementing requires all deps satisfied
    if from_status == "ready" and to_status == "implementing":
        if not _check_dependencies_satisfied(conn, ticket_id):
            raise DependenciesNotSatisfiedError(
                f"Ticket '{ticket_id}' has unsatisfied dependencies."
            )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build the UPDATE statement
    updates = {
        "status": to_status,
        "status_changed_at": now,
        "updated_at": now,
    }

    # Guard: human-gate requires a gate_reason
    if to_status == "human-gate":
        if not gate_reason:
            raise ValueError(
                "gate_reason is required when transitioning to human-gate."
            )
        updates["gate_reason"] = gate_reason
    elif from_status == "human-gate":
        updates["gate_reason"] = None

    # Guard: blocked requires a blocked_reason
    if to_status == "blocked":
        if not blocked_reason:
            raise ValueError(
                "blocked_reason is required when transitioning to blocked."
            )
        updates["blocked_reason"] = blocked_reason
    elif from_status == "blocked":
        updates["blocked_reason"] = None

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values())
    values.append(ticket_id)

    conn.execute(f"UPDATE tickets SET {set_clause} WHERE id = ?", values)

    # Record state transition
    conn.execute(
        "INSERT INTO state_transitions "
        "(ticket_id, from_status, to_status, triggered_by, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticket_id, from_status, to_status, triggered_by, reason),
    )

    conn.commit()

    if log_path:
        log_event(
            log_path,
            "STATE_TRANSITION",
            project_id=project_id,
            ticket_id=ticket_id,
            payload={
                "from": from_status,
                "to": to_status,
                "triggered_by": triggered_by,
                "reason": reason,
            },
        )
