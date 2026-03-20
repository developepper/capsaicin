"""Unblock pipeline for ``capsaicin ticket unblock`` (T24).

Returns a blocked ticket to ready for another attempt.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.orchestrator import reset_counters, set_idle
from capsaicin.state_machine import transition_ticket


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id() -> str:
    from ulid import ULID

    return str(ULID())


# ---------------------------------------------------------------------------
# Ticket selection
# ---------------------------------------------------------------------------


def select_unblock_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict:
    """Select a ticket for unblocking.

    Validates that the ticket exists and is in ``blocked`` status.

    Returns a dict with ticket row data.
    Raises ``ValueError`` if the ticket is not found or not blocked.
    """
    row = conn.execute(
        "SELECT id, project_id, title, description, status, blocked_reason "
        "FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")
    if row["status"] != "blocked":
        raise ValueError(
            f"Ticket '{ticket_id}' is in '{row['status']}' status; expected 'blocked'."
        )
    return dict(row)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def unblock_ticket(
    conn: sqlite3.Connection,
    project_id: str,
    ticket: dict,
    reset_cycles: bool = False,
    log_path: str | Path | None = None,
) -> str:
    """Execute the unblock pipeline for a ticket.

    Returns the final ticket status ('ready').
    """
    ticket_id = ticket["id"]
    now = _now()

    # --- Record decision ---
    decision_id = _generate_id()
    conn.execute(
        "INSERT INTO decisions (id, ticket_id, decision, rationale, created_at) "
        "VALUES (?, ?, 'unblock', NULL, ?)",
        (decision_id, ticket_id, now),
    )
    conn.commit()

    # --- Transition to ready (also clears blocked_reason) ---
    transition_ticket(
        conn,
        ticket_id,
        "ready",
        "human",
        reason="Unblocked by human.",
        log_path=log_path,
    )

    # --- Reset counters if requested ---
    if reset_cycles:
        reset_counters(conn, ticket_id)

    # --- Set orchestrator to idle ---
    set_idle(conn, project_id)

    if log_path:
        log_event(
            log_path,
            "TICKET_UNBLOCK",
            project_id=project_id,
            ticket_id=ticket_id,
            payload={"reset_cycles": reset_cycles},
        )

    return "ready"
