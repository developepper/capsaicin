"""Command service for ``ticket unblock``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult


def unblock(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    reset_cycles: bool = False,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Select a ticket and run the unblock pipeline.

    Returns a structured ``CommandResult`` with the final ticket status.
    """
    from capsaicin.ticket_unblock import select_unblock_ticket, unblock_ticket

    ticket = select_unblock_ticket(conn, ticket_id)

    final_status = unblock_ticket(
        conn=conn,
        project_id=project_id,
        ticket=ticket,
        reset_cycles=reset_cycles,
        log_path=log_path,
    )

    return CommandResult(
        ticket_id=ticket["id"],
        final_status=final_status,
    )
