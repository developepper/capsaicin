"""Command service for ``plan unblock``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult


def unblock(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    reason: str | None = None,
    log_path: str | Path | None = None,
) -> PlanningCommandResult:
    """Unblock an epic by transitioning it back to ``new``.

    Returns a structured ``PlanningCommandResult``.
    """
    from capsaicin.state_machine import transition_planned_epic

    transition_planned_epic(
        conn,
        epic_id,
        "new",
        "human",
        reason=reason or "operator unblock",
        log_path=log_path,
    )

    return PlanningCommandResult(
        epic_id=epic_id,
        final_status="new",
        detail=f"Epic {epic_id} unblocked",
    )
