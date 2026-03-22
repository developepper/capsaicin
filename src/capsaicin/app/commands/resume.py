"""Command service for ``resume``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult
from capsaicin.config import Config


def resume(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Run the resume pipeline based on orchestrator state.

    Returns a structured ``CommandResult`` with the action taken and detail.
    """
    from capsaicin.adapters.claude_code import ClaudeCodeAdapter
    from capsaicin.resume import resume_pipeline

    # Capture the active ticket id *before* resume_pipeline() runs,
    # because successful paths clear orchestrator state via set_idle().
    orch = conn.execute(
        "SELECT active_ticket_id FROM orchestrator_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    ticket_id = orch["active_ticket_id"] if orch and orch["active_ticket_id"] else ""

    impl_adapter = ClaudeCodeAdapter(command=config.implementer.command)
    review_adapter = ClaudeCodeAdapter(command=config.reviewer.command)

    action, detail = resume_pipeline(
        conn=conn,
        project_id=project_id,
        config=config,
        impl_adapter=impl_adapter,
        review_adapter=review_adapter,
        log_path=log_path,
    )

    return CommandResult(
        ticket_id=ticket_id,
        final_status=action,
        detail=detail,
    )
