"""Command service for ``plan defer``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult


def _select_deferable_epic(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
) -> dict:
    """Select an epic eligible for deferral (human-gate -> blocked).

    If *epic_id* is provided, validate it is in ``human-gate`` status.
    Otherwise, auto-select the first epic in ``human-gate`` status.
    """
    from capsaicin.queries import PLANNED_EPIC_COLUMNS, load_planned_epic

    if epic_id is not None:
        epic = load_planned_epic(conn, epic_id)
        if epic["status"] != "human-gate":
            raise ValueError(
                f"Epic '{epic_id}' is in '{epic['status']}' status; "
                "expected 'human-gate' for deferral."
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
            "No epic eligible for deferral (expected status 'human-gate')."
        )
    return dict(row)


def defer(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
    rationale: str | None = None,
    log_path: str | Path | None = None,
) -> PlanningCommandResult:
    """Defer (block) an epic from the human gate.

    Records a decision and transitions to ``blocked``.

    Returns a structured ``PlanningCommandResult``.
    """
    from capsaicin.queries import generate_id
    from capsaicin.state_machine import transition_planned_epic

    epic = _select_deferable_epic(conn, project_id, epic_id)

    conn.execute(
        "INSERT INTO decisions "
        "(id, epic_id, decision, rationale) "
        "VALUES (?, ?, 'defer', ?)",
        (generate_id(), epic["id"], rationale),
    )

    blocked_reason = rationale or "deferred by operator"
    transition_planned_epic(
        conn,
        epic["id"],
        "blocked",
        "human",
        reason=rationale or "human deferral",
        blocked_reason=blocked_reason,
        log_path=log_path,
    )

    return PlanningCommandResult(
        epic_id=epic["id"],
        final_status="blocked",
        detail=f"Epic {epic['id']} deferred",
    )
