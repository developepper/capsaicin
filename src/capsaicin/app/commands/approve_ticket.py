"""Command service for ``ticket approve``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult
from capsaicin.config import Config


def approve(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    ticket_id: str | None = None,
    rationale: str | None = None,
    force: bool = False,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Select a ticket and run the approval pipeline.

    Returns a structured ``CommandResult`` with the final ticket status.
    """
    from capsaicin.ticket_approve import (
        approve_ticket,
        select_approve_ticket,
    )

    ticket = select_approve_ticket(conn, ticket_id)

    final_status = approve_ticket(
        conn=conn,
        project_id=project_id,
        ticket=ticket,
        repo_path=config.project.repo_path,
        rationale=rationale,
        force=force,
        log_path=log_path,
    )

    return CommandResult(
        ticket_id=ticket["id"],
        final_status=final_status,
    )
