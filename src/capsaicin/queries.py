"""Shared query and helper functions used across pipeline modules."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from capsaicin.adapters.types import (
    AcceptanceCriterion,
    BackendEvidence,
    EvidenceRequirement,
    Finding,
)
from capsaicin.errors import PlannedEpicNotFoundError, TicketNotFoundError


def now_utc() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_id() -> str:
    """Generate a new ULID string."""
    from ulid import ULID

    return str(ULID())


def decode_text_list(value: str | list[str] | None) -> list[str]:
    """Decode a list stored as JSON, with newline fallback for legacy rows."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return [item for item in value.splitlines() if item]
    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    return [str(decoded)]


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
        "SELECT pf.id, pf.run_id, pf.epic_id, pf.planned_ticket_id, "
        "pf.severity, pf.category, pf.description, pf.fingerprint, "
        "pf.disposition, "
        "CASE WHEN pf.planned_ticket_id IS NULL THEN 'epic' ELSE 'ticket' END "
        "AS target_type, "
        "pt.sequence AS target_sequence "
        "FROM planning_findings pf "
        "LEFT JOIN planned_tickets pt ON pt.id = pf.planned_ticket_id "
        "WHERE pf.epic_id = ? AND pf.disposition = 'open'",
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


# ---------------------------------------------------------------------------
# Backend evidence helpers
# ---------------------------------------------------------------------------


def insert_backend_evidence(conn: sqlite3.Connection, evidence: BackendEvidence) -> str:
    """Insert a new backend evidence record. Returns the evidence ID."""
    now = now_utc()
    conn.execute(
        "INSERT INTO backend_evidence "
        "(id, epic_id, planned_ticket_id, evidence_type, title, "
        "body, command, stdout, stderr, structured_data, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            evidence.id,
            evidence.epic_id,
            evidence.planned_ticket_id,
            evidence.evidence_type,
            evidence.title,
            evidence.body,
            evidence.command,
            evidence.stdout,
            evidence.stderr,
            json.dumps(evidence.structured_data) if evidence.structured_data else None,
            now,
            now,
        ),
    )
    return evidence.id


def load_backend_evidence_for_epic(
    conn: sqlite3.Connection, epic_id: str
) -> list[BackendEvidence]:
    """Load all backend evidence for an epic."""
    rows = conn.execute(
        "SELECT id, epic_id, planned_ticket_id, evidence_type, title, "
        "body, command, stdout, stderr, structured_data, created_at, updated_at "
        "FROM backend_evidence WHERE epic_id = ? ORDER BY created_at",
        (epic_id,),
    ).fetchall()
    return [BackendEvidence.from_dict(dict(r)) for r in rows]


def load_backend_evidence_for_ticket(
    conn: sqlite3.Connection, planned_ticket_id: str
) -> list[BackendEvidence]:
    """Load all backend evidence for a planned ticket."""
    rows = conn.execute(
        "SELECT id, epic_id, planned_ticket_id, evidence_type, title, "
        "body, command, stdout, stderr, structured_data, created_at, updated_at "
        "FROM backend_evidence WHERE planned_ticket_id = ? ORDER BY created_at",
        (planned_ticket_id,),
    ).fetchall()
    return [BackendEvidence.from_dict(dict(r)) for r in rows]


def insert_evidence_requirement(
    conn: sqlite3.Connection, requirement: EvidenceRequirement
) -> str:
    """Insert a new evidence requirement. Returns the requirement ID."""
    now = now_utc()
    conn.execute(
        "INSERT INTO evidence_requirements "
        "(id, epic_id, planned_ticket_id, description, suggested_command, "
        "status, fulfilled_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            requirement.id,
            requirement.epic_id,
            requirement.planned_ticket_id,
            requirement.description,
            requirement.suggested_command,
            requirement.status,
            requirement.fulfilled_by,
            now,
            now,
        ),
    )
    _record_requirement_event(conn, requirement.id, "created")
    return requirement.id


def load_evidence_requirements_for_epic(
    conn: sqlite3.Connection, epic_id: str
) -> list[EvidenceRequirement]:
    """Load all evidence requirements for an epic."""
    rows = conn.execute(
        "SELECT id, epic_id, planned_ticket_id, description, suggested_command, "
        "status, fulfilled_by, created_at, updated_at "
        "FROM evidence_requirements WHERE epic_id = ? ORDER BY created_at",
        (epic_id,),
    ).fetchall()
    return [EvidenceRequirement.from_dict(dict(r)) for r in rows]


def load_evidence_requirements_for_ticket(
    conn: sqlite3.Connection, planned_ticket_id: str
) -> list[EvidenceRequirement]:
    """Load all evidence requirements for a planned ticket."""
    rows = conn.execute(
        "SELECT id, epic_id, planned_ticket_id, description, suggested_command, "
        "status, fulfilled_by, created_at, updated_at "
        "FROM evidence_requirements WHERE planned_ticket_id = ? ORDER BY created_at",
        (planned_ticket_id,),
    ).fetchall()
    return [EvidenceRequirement.from_dict(dict(r)) for r in rows]


def load_evidence_requirement_by_id(
    conn: sqlite3.Connection, requirement_id: str
) -> EvidenceRequirement | None:
    """Load a single evidence requirement by ID, or None if not found."""
    row = conn.execute(
        "SELECT id, epic_id, planned_ticket_id, description, suggested_command, "
        "status, fulfilled_by, created_at, updated_at "
        "FROM evidence_requirements WHERE id = ?",
        (requirement_id,),
    ).fetchone()
    if row is None:
        return None
    return EvidenceRequirement.from_dict(dict(row))


def load_backend_evidence_by_id(
    conn: sqlite3.Connection, evidence_id: str
) -> BackendEvidence | None:
    """Load a single backend evidence record by ID, or None if not found."""
    row = conn.execute(
        "SELECT id, epic_id, planned_ticket_id, evidence_type, title, "
        "body, command, stdout, stderr, structured_data, created_at, updated_at "
        "FROM backend_evidence WHERE id = ?",
        (evidence_id,),
    ).fetchone()
    if row is None:
        return None
    return BackendEvidence.from_dict(dict(row))


def clear_evidence_from_requirements(
    conn: sqlite3.Connection, evidence_id: str
) -> None:
    """Reset any requirements fulfilled by this evidence back to pending."""
    now = now_utc()
    # Find affected requirements before updating
    affected = conn.execute(
        "SELECT id FROM evidence_requirements WHERE fulfilled_by = ?",
        (evidence_id,),
    ).fetchall()
    conn.execute(
        "UPDATE evidence_requirements SET status = 'pending', "
        "fulfilled_by = NULL, updated_at = ? WHERE fulfilled_by = ?",
        (now, evidence_id),
    )
    for row in affected:
        _record_requirement_event(conn, row["id"], "reset_to_pending")


def delete_backend_evidence(conn: sqlite3.Connection, evidence_id: str) -> None:
    """Delete a backend evidence record by ID.

    Resets any requirements fulfilled by this evidence back to pending
    (clearing the FK reference). Audit trail rows in agent_run_evidence
    and evidence_requirement_events are preserved (their evidence_id
    columns have no FK constraint) so the audit trail remains durable
    even after evidence deletion.
    """
    clear_evidence_from_requirements(conn, evidence_id)
    conn.execute("DELETE FROM backend_evidence WHERE id = ?", (evidence_id,))


def fulfill_evidence_requirement(
    conn: sqlite3.Connection, requirement_id: str, evidence_id: str
) -> None:
    """Mark a requirement as fulfilled by linking it to an evidence record."""
    now = now_utc()
    conn.execute(
        "UPDATE evidence_requirements SET status = 'fulfilled', "
        "fulfilled_by = ?, updated_at = ? WHERE id = ?",
        (evidence_id, now, requirement_id),
    )
    _record_requirement_event(
        conn, requirement_id, "satisfied", evidence_id=evidence_id
    )


def waive_evidence_requirement(conn: sqlite3.Connection, requirement_id: str) -> None:
    """Mark a requirement as waived."""
    now = now_utc()
    conn.execute(
        "UPDATE evidence_requirements SET status = 'waived', updated_at = ? WHERE id = ?",
        (now, requirement_id),
    )
    _record_requirement_event(conn, requirement_id, "waived")


def _record_requirement_event(
    conn: sqlite3.Connection,
    requirement_id: str,
    event_type: str,
    evidence_id: str | None = None,
) -> None:
    """Insert an audit event for an evidence requirement status change."""
    conn.execute(
        "INSERT INTO evidence_requirement_events "
        "(id, requirement_id, event_type, evidence_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (generate_id(), requirement_id, event_type, evidence_id, now_utc()),
    )


def record_run_evidence(
    conn: sqlite3.Connection,
    run_id: str,
    evidence_ids: list[str],
) -> None:
    """Record which evidence records were included in an agent run's prompt."""
    for eid in evidence_ids:
        conn.execute(
            "INSERT OR IGNORE INTO agent_run_evidence (run_id, evidence_id) "
            "VALUES (?, ?)",
            (run_id, eid),
        )


def load_evidence_for_run(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """Load evidence records that were included in an agent run's prompt.

    Uses LEFT JOIN so that orphaned audit rows (evidence deleted after
    the run) still appear with a placeholder title.
    """
    rows = conn.execute(
        "SELECT are_.evidence_id AS id, "
        "COALESCE(be.title, '(deleted evidence)') AS title, "
        "COALESCE(be.evidence_type, 'unknown') AS evidence_type "
        "FROM agent_run_evidence are_ "
        "LEFT JOIN backend_evidence be ON be.id = are_.evidence_id "
        "WHERE are_.run_id = ? ORDER BY COALESCE(be.created_at, '')",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_runs_for_evidence(conn: sqlite3.Connection, evidence_id: str) -> list[dict]:
    """Load agent runs that consumed a given evidence record."""
    rows = conn.execute(
        "SELECT ar.id, ar.role, ar.exit_status, ar.started_at, "
        "ar.cycle_number, ar.attempt_number "
        "FROM agent_run_evidence are_ "
        "JOIN agent_runs ar ON ar.id = are_.run_id "
        "WHERE are_.evidence_id = ? ORDER BY ar.started_at",
        (evidence_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_evidence_timeline(conn: sqlite3.Connection, epic_id: str) -> list[dict]:
    """Build a chronological evidence timeline for an epic.

    Returns a list of timeline events sorted by timestamp, combining:
    - Evidence creation events
    - Requirement status-change events
    - Evidence consumption by agent runs
    """
    events: list[dict] = []

    # Evidence creation
    ev_rows = conn.execute(
        "SELECT id, title, evidence_type, created_at "
        "FROM backend_evidence WHERE epic_id = ? ORDER BY created_at",
        (epic_id,),
    ).fetchall()
    for r in ev_rows:
        events.append(
            {
                "timestamp": r["created_at"],
                "event_type": "evidence_added",
                "title": r["title"],
                "detail": r["evidence_type"].replace("_", " "),
                "evidence_id": r["id"],
            }
        )

    # Requirement status-change events
    req_rows = conn.execute(
        "SELECT ere.event_type, ere.evidence_id, ere.created_at, "
        "er.description AS req_description "
        "FROM evidence_requirement_events ere "
        "JOIN evidence_requirements er ON er.id = ere.requirement_id "
        "WHERE er.epic_id = ? ORDER BY ere.created_at",
        (epic_id,),
    ).fetchall()
    for r in req_rows:
        events.append(
            {
                "timestamp": r["created_at"],
                "event_type": f"requirement_{r['event_type']}",
                "title": r["req_description"],
                "detail": r["event_type"],
                "evidence_id": r["evidence_id"],
            }
        )

    # Evidence consumed by runs — planning runs (epic_id on agent_runs)
    # and implementation runs (ticket_id -> planned_tickets -> epic).
    # LEFT JOIN backend_evidence so orphaned audit rows (deleted evidence)
    # still appear on the timeline.
    run_rows = conn.execute(
        "SELECT ar.started_at, ar.role, ar.id AS run_id, "
        "COALESCE(be.title, '(deleted evidence)') AS evidence_title, "
        "are_.evidence_id AS evidence_id "
        "FROM agent_run_evidence are_ "
        "JOIN agent_runs ar ON ar.id = are_.run_id "
        "LEFT JOIN backend_evidence be ON be.id = are_.evidence_id "
        "WHERE ar.epic_id = ? "
        "UNION "
        "SELECT ar.started_at, ar.role, ar.id AS run_id, "
        "COALESCE(be.title, '(deleted evidence)') AS evidence_title, "
        "are_.evidence_id AS evidence_id "
        "FROM agent_run_evidence are_ "
        "JOIN agent_runs ar ON ar.id = are_.run_id "
        "LEFT JOIN backend_evidence be ON be.id = are_.evidence_id "
        "JOIN tickets t ON t.id = ar.ticket_id "
        "JOIN planned_tickets pt ON pt.id = t.planned_ticket_id "
        "WHERE pt.epic_id = ? "
        "ORDER BY started_at",
        (epic_id, epic_id),
    ).fetchall()
    for r in run_rows:
        events.append(
            {
                "timestamp": r["started_at"],
                "event_type": "evidence_consumed",
                "title": r["evidence_title"],
                "detail": f"consumed by {r['role']} run",
                "evidence_id": r["evidence_id"],
                "run_id": r["run_id"],
            }
        )

    # Sort by timestamp
    events.sort(key=lambda e: e["timestamp"])
    return events


def check_evidence_completeness(
    conn: sqlite3.Connection, epic_id: str
) -> list[EvidenceRequirement]:
    """Return pending (unsatisfied, un-waived) evidence requirements for an epic.

    Returns an empty list if all requirements are satisfied or waived,
    meaning the epic is not blocked by missing evidence.
    """
    rows = conn.execute(
        "SELECT id, epic_id, planned_ticket_id, description, suggested_command, "
        "status, fulfilled_by, created_at, updated_at "
        "FROM evidence_requirements WHERE epic_id = ? AND status = 'pending' "
        "ORDER BY created_at",
        (epic_id,),
    ).fetchall()
    return [EvidenceRequirement.from_dict(dict(r)) for r in rows]
