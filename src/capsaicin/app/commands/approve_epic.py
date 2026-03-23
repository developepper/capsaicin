"""Command service for ``plan approve``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult


def _select_approvable_epic(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
) -> dict:
    """Select an epic eligible for approval (human-gate -> approved).

    If *epic_id* is provided, validate it is in ``human-gate`` status.
    Otherwise, auto-select the first epic in ``human-gate`` status.
    """
    from capsaicin.queries import PLANNED_EPIC_COLUMNS, load_planned_epic

    if epic_id is not None:
        epic = load_planned_epic(conn, epic_id)
        if epic["status"] != "human-gate":
            raise ValueError(
                f"Epic '{epic_id}' is in '{epic['status']}' status; "
                "expected 'human-gate' for approval."
            )
        return epic

    row = conn.execute(
        f"SELECT {PLANNED_EPIC_COLUMNS} FROM planned_epics "
        "WHERE project_id = ? AND status = 'human-gate' "
        "ORDER BY status_changed_at LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            "No epic eligible for approval (expected status 'human-gate')."
        )
    return dict(row)


def approve(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
    rationale: str | None = None,
    log_path: str | Path | None = None,
) -> PlanningCommandResult:
    """Approve an epic at the human gate.

    Records a decision and transitions to ``approved``.

    Returns a structured ``PlanningCommandResult``.
    """
    from capsaicin.queries import generate_id
    from capsaicin.state_machine import transition_planned_epic

    epic = _select_approvable_epic(conn, project_id, epic_id)

    conn.execute(
        "INSERT INTO decisions "
        "(id, epic_id, decision, rationale) "
        "VALUES (?, ?, 'approve', ?)",
        (generate_id(), epic["id"], rationale),
    )

    transition_planned_epic(
        conn,
        epic["id"],
        "approved",
        "human",
        reason=rationale or "human approval",
        log_path=log_path,
    )

    return PlanningCommandResult(
        epic_id=epic["id"],
        final_status="approved",
        detail=f"Epic {epic['id']} approved",
    )
