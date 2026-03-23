"""Completion pipeline for ``capsaicin ticket complete``.

Explicit human action to transition a ``pr-ready`` ticket to ``done``,
unblocking any dependent tickets.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.state_machine import transition_ticket


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id() -> str:
    from ulid import ULID

    return str(ULID())


# ---------------------------------------------------------------------------
# Ticket selection
# ---------------------------------------------------------------------------


def select_complete_ticket(
    conn: sqlite3.Connection, ticket_id: str | None = None
) -> dict:
    """Select a ticket for completion.

    If *ticket_id* is given, validate that it exists and is in ``pr-ready``.
    Otherwise auto-select the first ``pr-ready`` ticket ordered by
    ``status_changed_at``.

    Returns a dict with ticket row data.
    Raises ``ValueError`` if no eligible ticket is found.
    """
    from capsaicin.queries import TICKET_COLUMNS, load_ticket

    from capsaicin.errors import InvalidStatusError, NoEligibleTicketError

    if ticket_id:
        ticket = load_ticket(conn, ticket_id)
        if ticket["status"] != "pr-ready":
            raise InvalidStatusError(ticket_id, ticket["status"], "pr-ready")
        return ticket

    row = conn.execute(
        f"SELECT {TICKET_COLUMNS} "
        "FROM tickets WHERE status = 'pr-ready' "
        "ORDER BY status_changed_at"
    ).fetchone()

    if row is None:
        raise NoEligibleTicketError("No ticket found in 'pr-ready' status.")

    return dict(row)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def complete_ticket(
    conn: sqlite3.Connection,
    project_id: str,
    ticket: dict,
    rationale: str | None = None,
    log_path: str | Path | None = None,
) -> str:
    """Execute the completion pipeline for a pr-ready ticket.

    Records a ``complete`` decision and transitions ``pr-ready -> done``.

    Returns the final ticket status (``done``).
    """
    ticket_id = ticket["id"]

    # --- Record decision ---
    decision_id = _generate_id()
    conn.execute(
        "INSERT INTO decisions (id, ticket_id, decision, rationale, created_at) "
        "VALUES (?, ?, 'complete', ?, ?)",
        (decision_id, ticket_id, rationale, _now()),
    )
    conn.commit()

    # --- Transition to done ---
    transition_ticket(
        conn,
        ticket_id,
        "done",
        "human",
        reason=rationale or "Completed by human.",
        log_path=log_path,
    )

    if log_path:
        log_event(
            log_path,
            "DECISION",
            project_id=project_id,
            ticket_id=ticket_id,
            payload={
                "decision": "complete",
                "rationale": rationale,
            },
        )

    return "done"
