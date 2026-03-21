"""Shared query and helper functions used across pipeline modules."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from capsaicin.adapters.types import AcceptanceCriterion, Finding
from capsaicin.errors import TicketNotFoundError


def now_utc() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_id() -> str:
    """Generate a new ULID string."""
    from ulid import ULID

    return str(ULID())


def load_criteria(
    conn: sqlite3.Connection, ticket_id: str
) -> list[AcceptanceCriterion]:
    """Load acceptance criteria for a ticket."""
    rows = conn.execute(
        "SELECT id, description, status FROM acceptance_criteria WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchall()
    return [
        AcceptanceCriterion(
            id=r["id"], description=r["description"], status=r["status"]
        )
        for r in rows
    ]


def load_open_findings(conn: sqlite3.Connection, ticket_id: str) -> list[Finding]:
    """Load open findings for a ticket."""
    rows = conn.execute(
        "SELECT severity, category, description, location, "
        "acceptance_criterion_id, disposition "
        "FROM findings WHERE ticket_id = ? AND disposition = 'open'",
        (ticket_id,),
    ).fetchall()
    return [
        Finding(
            severity=r["severity"],
            category=r["category"],
            description=r["description"],
            location=r["location"],
            acceptance_criterion_id=r["acceptance_criterion_id"],
            disposition=r["disposition"],
        )
        for r in rows
    ]


# Superset of columns needed by all ticket-selection and reload queries.
TICKET_COLUMNS = (
    "id, project_id, title, description, status, "
    "gate_reason, blocked_reason, "
    "current_cycle, current_impl_attempt, current_review_attempt, "
    "created_at, status_changed_at"
)


def load_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict:
    """Load a ticket by ID, returning a dict with all common columns.

    Raises ``ValueError`` if the ticket does not exist.
    """
    row = conn.execute(
        f"SELECT {TICKET_COLUMNS} FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise TicketNotFoundError(ticket_id)
    return dict(row)


def get_impl_run_id(conn: sqlite3.Connection, ticket_id: str) -> str:
    """Get the most recent implementer run ID for a ticket."""
    row = conn.execute(
        "SELECT id FROM agent_runs "
        "WHERE ticket_id = ? AND role = 'implementer' "
        "ORDER BY started_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No implementer run found for ticket '{ticket_id}'.")
    return row["id"]
