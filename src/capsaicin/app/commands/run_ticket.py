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
    from capsaicin.adapters.registry import build_adapter_from_config
    from capsaicin.resolver import lookup_epic_id_for_ticket, resolve_adapter_config
    from capsaicin.ticket_run import run_implementation_pipeline, select_ticket

    ticket = select_ticket(conn, ticket_id)
    epic_id = lookup_epic_id_for_ticket(conn, ticket["id"])

    adapter_config = resolve_adapter_config(
        config,
        role="implementer",
        conn=conn,
        ticket_id=ticket["id"],
        epic_id=epic_id,
    )
    adapter = build_adapter_from_config(adapter_config)
    final_status = run_implementation_pipeline(
        conn=conn,
        project_id=project_id,
        ticket=ticket,
        config=config,
        adapter=adapter,
        log_path=log_path,
        epic_id=epic_id,
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
