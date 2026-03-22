"""Command service for ``ticket review``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult
from capsaicin.config import Config


def review(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    ticket_id: str | None = None,
    allow_drift: bool = False,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Select a ticket and run the review pipeline.

    Returns a structured ``CommandResult`` with the final ticket status.
    """
    from capsaicin.adapters.claude_code import ClaudeCodeAdapter
    from capsaicin.ticket_review import run_review_pipeline, select_review_ticket

    ticket = select_review_ticket(conn, ticket_id)

    adapter = ClaudeCodeAdapter(command=config.reviewer.command)
    final_status = run_review_pipeline(
        conn=conn,
        project_id=project_id,
        ticket=ticket,
        config=config,
        adapter=adapter,
        allow_drift=allow_drift,
        log_path=log_path,
    )

    refreshed = conn.execute(
        "SELECT gate_reason, blocked_reason FROM tickets WHERE id = ?",
        (ticket["id"],),
    ).fetchone()

    return CommandResult(
        ticket_id=ticket["id"],
        final_status=final_status,
        detail=f"Running review for ticket {ticket['id']}: {ticket['title']}",
        gate_reason=refreshed["gate_reason"] if refreshed else None,
        blocked_reason=refreshed["blocked_reason"] if refreshed else None,
    )
