"""Command service for ``ticket complete``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult


def complete(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str | None = None,
    rationale: str | None = None,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Select a ticket and run the completion pipeline.

    Returns a structured ``CommandResult`` with the final ticket status.
    """
    from capsaicin.ticket_complete import (
        complete_ticket,
        select_complete_ticket,
    )

    ticket = select_complete_ticket(conn, ticket_id)

    final_status = complete_ticket(
        conn=conn,
        project_id=project_id,
        ticket=ticket,
        rationale=rationale,
        log_path=log_path,
    )

    return CommandResult(
        ticket_id=ticket["id"],
        final_status=final_status,
    )
