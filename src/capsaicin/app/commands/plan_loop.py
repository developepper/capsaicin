"""Command service for ``plan loop``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult
from capsaicin.config import Config


def plan_loop(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    epic_id: str | None = None,
    max_cycles: int | None = None,
    log_path: str | Path | None = None,
) -> PlanningCommandResult:
    """Run the planning draft-review-revise loop.

    Returns a structured ``PlanningCommandResult`` with the final status and detail.
    """
    from capsaicin.adapters.claude_code import ClaudeCodeAdapter
    from capsaicin.planning_loop import run_planning_loop
    from capsaicin.planning_run import select_epic_for_draft

    # Resolve the epic before entering the loop so the identity is
    # captured regardless of whether the caller passed an explicit ID.
    selected = select_epic_for_draft(conn, project_id, epic_id)
    resolved_epic_id = selected["id"]

    draft_adapter = ClaudeCodeAdapter(command=config.implementer.command)
    review_adapter = ClaudeCodeAdapter(command=config.reviewer.command)

    final_status, detail = run_planning_loop(
        conn=conn,
        project_id=project_id,
        config=config,
        draft_adapter=draft_adapter,
        review_adapter=review_adapter,
        epic_id=resolved_epic_id,
        max_cycles=max_cycles,
        log_path=log_path,
    )

    # Reload epic for gate/blocked reasons
    row = conn.execute(
        "SELECT gate_reason, blocked_reason FROM planned_epics WHERE id = ?",
        (resolved_epic_id,),
    ).fetchone()

    return PlanningCommandResult(
        epic_id=resolved_epic_id,
        final_status=final_status,
        detail=detail,
        gate_reason=row["gate_reason"] if row else None,
        blocked_reason=row["blocked_reason"] if row else None,
    )
