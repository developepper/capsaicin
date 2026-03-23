"""Shared query and helper functions used across pipeline modules."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from capsaicin.adapters.types import AcceptanceCriterion, Finding
from capsaicin.errors import PlannedEpicNotFoundError, TicketNotFoundError


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


# ---------------------------------------------------------------------------
# Planning query helpers
# ---------------------------------------------------------------------------

PLANNED_EPIC_COLUMNS = (
    "id, project_id, problem_statement, title, summary, success_outcome, "
    "sequencing_notes, current_cycle, current_draft_attempt, "
    "current_review_attempt, blocked_reason, gate_reason, status, "
    "materialized_path, status_changed_at, created_at, updated_at"
)


def load_planned_epic(conn: sqlite3.Connection, epic_id: str) -> dict:
    """Load a planned epic by ID, returning a dict with all columns.

    Raises ``PlannedEpicNotFoundError`` if the epic does not exist.
    """
    row = conn.execute(
        f"SELECT {PLANNED_EPIC_COLUMNS} FROM planned_epics WHERE id = ?",
        (epic_id,),
    ).fetchone()
    if row is None:
        raise PlannedEpicNotFoundError(epic_id)
    return dict(row)


def load_planned_tickets(conn: sqlite3.Connection, epic_id: str) -> list[dict]:
    """Load all planned tickets for an epic, ordered by sequence."""
    rows = conn.execute(
        "SELECT id, epic_id, sequence, title, goal, scope, non_goals, "
        "references_, implementation_notes, created_at, updated_at "
        "FROM planned_tickets WHERE epic_id = ? ORDER BY sequence",
        (epic_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_planned_ticket_criteria(
    conn: sqlite3.Connection, planned_ticket_id: str
) -> list[dict]:
    """Load acceptance criteria for a planned ticket."""
    rows = conn.execute(
        "SELECT id, description FROM planned_ticket_criteria "
        "WHERE planned_ticket_id = ?",
        (planned_ticket_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_open_planning_findings(conn: sqlite3.Connection, epic_id: str) -> list[dict]:
    """Load open planning findings for an epic."""
    rows = conn.execute(
        "SELECT id, run_id, epic_id, planned_ticket_id, severity, category, "
        "description, fingerprint, disposition "
        "FROM planning_findings WHERE epic_id = ? AND disposition = 'open'",
        (epic_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_planning_run_id(conn: sqlite3.Connection, epic_id: str) -> str:
    """Get the most recent planner run ID for an epic."""
    row = conn.execute(
        "SELECT id FROM agent_runs "
        "WHERE epic_id = ? AND role = 'planner' "
        "ORDER BY started_at DESC LIMIT 1",
        (epic_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No planner run found for epic '{epic_id}'.")
    return row["id"]
