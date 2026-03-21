"""Defer/abandon pipeline for ``capsaicin ticket defer`` (T23).

Defers a ticket from human-gate to blocked, or abandons it to done.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.orchestrator import set_idle
from capsaicin.state_machine import transition_ticket


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id() -> str:
    from ulid import ULID

    return str(ULID())


# ---------------------------------------------------------------------------
# Ticket selection
# ---------------------------------------------------------------------------


def select_defer_ticket(conn: sqlite3.Connection, ticket_id: str | None = None) -> dict:
    """Select a ticket for deferral.

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
# Pipeline
# ---------------------------------------------------------------------------


def defer_ticket(
    conn: sqlite3.Connection,
    project_id: str,
    ticket: dict,
    rationale: str | None = None,
    abandon: bool = False,
    log_path: str | Path | None = None,
) -> str:
    """Execute the defer/abandon pipeline for a ticket.

    Without *abandon*: records a ``defer`` decision and transitions to
    ``blocked``.  With *abandon*: records a ``reject`` decision and
    transitions through ``blocked`` to ``done``.

    Returns the final ticket status.
    """
    ticket_id = ticket["id"]
    now = _now()

    if abandon:
        return _abandon(conn, project_id, ticket_id, rationale, now, log_path)

    return _defer(conn, project_id, ticket_id, rationale, now, log_path)


def _defer(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    rationale: str | None,
    now: str,
    log_path: str | Path | None,
) -> str:
    """Defer: human-gate -> blocked."""
    # Record decision
    decision_id = _generate_id()
    conn.execute(
        "INSERT INTO decisions (id, ticket_id, decision, rationale, created_at) "
        "VALUES (?, ?, 'defer', ?, ?)",
        (decision_id, ticket_id, rationale, now),
    )
    conn.commit()

    # Transition to blocked
    blocked_reason = rationale or "Deferred by human."
    transition_ticket(
        conn,
        ticket_id,
        "blocked",
        "human",
        reason=blocked_reason,
        blocked_reason=blocked_reason,
        log_path=log_path,
    )

    # Set orchestrator to idle
    set_idle(conn, project_id)

    if log_path:
        log_event(
            log_path,
            "DECISION",
            project_id=project_id,
            ticket_id=ticket_id,
            payload={"decision": "defer", "rationale": rationale},
        )

    return "blocked"


def _abandon(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    rationale: str | None,
    now: str,
    log_path: str | Path | None,
) -> str:
    """Abandon: human-gate -> blocked -> done."""
    # Record decision
    decision_id = _generate_id()
    conn.execute(
        "INSERT INTO decisions (id, ticket_id, decision, rationale, created_at) "
        "VALUES (?, ?, 'reject', ?, ?)",
        (decision_id, ticket_id, rationale, now),
    )
    conn.commit()

    # Transition human-gate -> blocked
    blocked_reason = rationale or "Abandoned by human."
    transition_ticket(
        conn,
        ticket_id,
        "blocked",
        "human",
        reason=blocked_reason,
        blocked_reason=blocked_reason,
        log_path=log_path,
    )

    # Transition blocked -> done
    transition_ticket(
        conn,
        ticket_id,
        "done",
        "human",
        reason=rationale or "Abandoned by human.",
        log_path=log_path,
    )

    # Set orchestrator to idle
    set_idle(conn, project_id)

    if log_path:
        log_event(
            log_path,
            "DECISION",
            project_id=project_id,
            ticket_id=ticket_id,
            payload={"decision": "reject", "rationale": rationale, "abandon": True},
        )

    return "done"
