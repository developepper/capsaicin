"""Command service for ``ticket defer``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult


def defer(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str | None = None,
    rationale: str | None = None,
    abandon: bool = False,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Select a ticket and run the defer/abandon pipeline.

    Returns a structured ``CommandResult`` with the final ticket status.
    """
    from capsaicin.ticket_defer import defer_ticket, select_defer_ticket

    ticket = select_defer_ticket(conn, ticket_id)

    final_status = defer_ticket(
        conn=conn,
        project_id=project_id,
        ticket=ticket,
        rationale=rationale,
        abandon=abandon,
        log_path=log_path,
    )

    return CommandResult(
        ticket_id=ticket["id"],
        final_status=final_status,
    )
