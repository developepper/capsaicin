"""Command service for ``plan review``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult


def _select_reviewable_epic(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
) -> dict:
    """Select an epic eligible for review.

    If *epic_id* is provided, validate it is in ``drafting`` status.
    Otherwise, auto-select the first epic in ``drafting`` status.
    """
    from capsaicin.queries import PLANNED_EPIC_COLUMNS, load_planned_epic

    if epic_id is not None:
        epic = load_planned_epic(conn, epic_id)
        if epic["status"] != "drafting":
            raise ValueError(
                f"Epic '{epic_id}' is in '{epic['status']}' status; "
                "expected 'drafting' for review."
            )
        return epic

    row = conn.execute(
        f"SELECT {PLANNED_EPIC_COLUMNS} FROM planned_epics "
        "WHERE project_id = ? AND status = 'drafting' "
        "ORDER BY created_at LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError("No epic eligible for review (expected status 'drafting').")
    return dict(row)


def review(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
    log_path: str | Path | None = None,
) -> PlanningCommandResult:
    """Select an epic and transition it to in-review.

    This is the manual stepping command; the actual planning reviewer
    pipeline invocation will be wired by T04 loop orchestration.

    Returns a structured ``PlanningCommandResult``.
    """
    from capsaicin.state_machine import transition_planned_epic

    epic = _select_reviewable_epic(conn, project_id, epic_id)

    transition_planned_epic(
        conn,
        epic["id"],
        "in-review",
        "system",
        reason="manual review",
        log_path=log_path,
    )

    return PlanningCommandResult(
        epic_id=epic["id"],
        final_status="in-review",
        detail=f"Epic {epic['id']} transitioned to in-review",
    )
