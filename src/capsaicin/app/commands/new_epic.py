"""Command service for ``plan new``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult


def new_epic(
    conn: sqlite3.Connection,
    project_id: str,
    problem_statement: str,
    log_path: str | Path | None = None,
) -> PlanningCommandResult:
    """Create a new planned epic from a problem statement.

    Returns a structured ``PlanningCommandResult`` with the initial status.
    """
    from capsaicin.activity_log import log_event
    from capsaicin.queries import generate_id, now_utc

    epic_id = generate_id()
    now = now_utc()

    conn.execute(
        "INSERT INTO planned_epics "
        "(id, project_id, problem_statement, status, created_at, updated_at, status_changed_at) "
        "VALUES (?, ?, ?, 'new', ?, ?, ?)",
        (epic_id, project_id, problem_statement, now, now, now),
    )

    conn.execute(
        "INSERT INTO state_transitions "
        "(epic_id, from_status, to_status, triggered_by, reason) "
        "VALUES (?, 'null', 'new', 'human', 'epic created')",
        (epic_id,),
    )

    conn.commit()

    if log_path:
        log_event(
            log_path,
            "EPIC_CREATED",
            project_id=project_id,
            payload={"epic_id": epic_id},
        )

    return PlanningCommandResult(
        epic_id=epic_id,
        final_status="new",
        detail=f"Created planned epic {epic_id}",
    )
