"""Command service for ``ticket revise``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult


def revise(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str | None = None,
    add_findings: list[str] | None = None,
    reset_cycles: bool = False,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Select a ticket and run the revision pipeline.

    Returns a structured ``CommandResult`` with the final ticket status.
    """
    from capsaicin.ticket_revise import revise_ticket, select_revise_ticket

    ticket = select_revise_ticket(conn, ticket_id)

    final_status = revise_ticket(
        conn=conn,
        project_id=project_id,
        ticket=ticket,
        add_findings=add_findings,
        reset_cycles=reset_cycles,
        log_path=log_path,
    )

    return CommandResult(
        ticket_id=ticket["id"],
        final_status=final_status,
        detail=(f"Added {len(add_findings)} finding(s)" if add_findings else None),
    )
