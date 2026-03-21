"""Command service for ``ticket run``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult
from capsaicin.config import Config


def run(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    ticket_id: str | None = None,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Select a ticket and run the implementation pipeline.

    Returns a structured ``CommandResult`` with the final ticket status.
    """
    from capsaicin.adapters.claude_code import ClaudeCodeAdapter
    from capsaicin.ticket_run import run_implementation_pipeline, select_ticket

    ticket = select_ticket(conn, ticket_id)

    adapter = ClaudeCodeAdapter(command=config.implementer.command)
    final_status = run_implementation_pipeline(
        conn=conn,
        project_id=project_id,
        ticket=ticket,
        config=config,
        adapter=adapter,
        log_path=log_path,
    )

    # Reload ticket for gate/blocked reason
    refreshed = conn.execute(
        "SELECT gate_reason, blocked_reason FROM tickets WHERE id = ?",
        (ticket["id"],),
    ).fetchone()

    return CommandResult(
        ticket_id=ticket["id"],
        final_status=final_status,
        detail=f"Running implementation for ticket {ticket['id']}: {ticket['title']}",
        gate_reason=refreshed["gate_reason"] if refreshed else None,
        blocked_reason=refreshed["blocked_reason"] if refreshed else None,
    )
