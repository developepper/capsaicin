"""Planning summary read model — project-level planning overview."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class PlanningSummaryData:
    """Structured planning summary for operator views."""

    total_epics: int
    counts_by_status: dict[str, int] = field(default_factory=dict)
    human_gate_epics: list[dict] = field(default_factory=list)
    blocked_epics: list[dict] = field(default_factory=list)
    active_epics: list[dict] = field(default_factory=list)


def get_epic_counts_by_status(
    conn: sqlite3.Connection, project_id: str
) -> dict[str, int]:
    """Return planned epic counts grouped by status."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM planned_epics "
        "WHERE project_id = ? GROUP BY status",
        (project_id,),
    ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}


def get_human_gate_epics(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Return planned epics in human-gate status."""
    rows = conn.execute(
        "SELECT id, problem_statement, title, gate_reason, status_changed_at "
        "FROM planned_epics "
        "WHERE project_id = ? AND status = 'human-gate' "
        "ORDER BY status_changed_at",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_blocked_epics(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Return planned epics in blocked status."""
    rows = conn.execute(
        "SELECT id, problem_statement, title, blocked_reason, status_changed_at "
        "FROM planned_epics "
        "WHERE project_id = ? AND status = 'blocked' "
        "ORDER BY status_changed_at",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_active_epics(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Return planned epics in active states (new, drafting, in-review, revise)."""
    rows = conn.execute(
        "SELECT id, problem_statement, title, status, status_changed_at "
        "FROM planned_epics "
        "WHERE project_id = ? AND status IN ('new', 'drafting', 'in-review', 'revise') "
        "ORDER BY created_at",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_planning_summary(
    conn: sqlite3.Connection, project_id: str
) -> PlanningSummaryData:
    """Build a structured planning summary for the project."""
    counts = get_epic_counts_by_status(conn, project_id)
    total = sum(counts.values())

    return PlanningSummaryData(
        total_epics=total,
        counts_by_status=counts,
        human_gate_epics=get_human_gate_epics(conn, project_id),
        blocked_epics=get_blocked_epics(conn, project_id),
        active_epics=get_active_epics(conn, project_id),
    )
