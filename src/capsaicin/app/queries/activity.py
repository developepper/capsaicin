"""Recent activity read model — recent agent runs across the project."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class RecentRun:
    """A single recent agent run."""

    run_id: str
    ticket_id: str
    ticket_title: str
    role: str
    exit_status: str
    verdict: str | None = None
    duration_seconds: float | None = None
    started_at: str = ""
    cycle_number: int = 0
    attempt_number: int = 0


def get_recent_activity(
    conn: sqlite3.Connection,
    project_id: str,
    limit: int = 20,
) -> list[RecentRun]:
    """Return the most recent agent runs for the project."""
    rows = conn.execute(
        "SELECT ar.id, ar.ticket_id, t.title, ar.role, ar.exit_status, "
        "ar.verdict, ar.duration_seconds, ar.started_at, "
        "ar.cycle_number, ar.attempt_number "
        "FROM agent_runs ar "
        "JOIN tickets t ON t.id = ar.ticket_id "
        "WHERE t.project_id = ? "
        "ORDER BY ar.started_at DESC "
        "LIMIT ?",
        (project_id, limit),
    ).fetchall()

    return [
        RecentRun(
            run_id=r["id"],
            ticket_id=r["ticket_id"],
            ticket_title=r["title"],
            role=r["role"],
            exit_status=r["exit_status"],
            verdict=r["verdict"],
            duration_seconds=r["duration_seconds"],
            started_at=r["started_at"],
            cycle_number=r["cycle_number"],
            attempt_number=r["attempt_number"],
        )
        for r in rows
    ]
