"""Command service for ``loop``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import CommandResult
from capsaicin.config import Config


def loop(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    ticket_id: str | None = None,
    max_cycles: int | None = None,
    log_path: str | Path | None = None,
) -> CommandResult:
    """Run the implement-review-revise loop.

    Returns a structured ``CommandResult`` with the final status and detail.
    """
    from capsaicin.adapters.registry import build_adapter_from_config
    from capsaicin.loop import run_loop, select_ticket_for_loop
    from capsaicin.resolver import lookup_epic_id_for_ticket, resolve_adapter_config

    # Resolve the ticket before entering the loop so the identity is
    # captured regardless of whether the caller passed an explicit ID.
    selected = select_ticket_for_loop(conn, ticket_id)
    resolved_ticket_id = selected["id"]
    epic_id = lookup_epic_id_for_ticket(conn, resolved_ticket_id)

    impl_config = resolve_adapter_config(
        config,
        role="implementer",
        conn=conn,
        ticket_id=resolved_ticket_id,
        epic_id=epic_id,
    )
    review_config = resolve_adapter_config(
        config,
        role="reviewer",
        conn=conn,
        ticket_id=resolved_ticket_id,
        epic_id=epic_id,
    )
    impl_adapter = build_adapter_from_config(impl_config)
    review_adapter = build_adapter_from_config(review_config)

    final_status, detail = run_loop(
        conn=conn,
        project_id=project_id,
        config=config,
        impl_adapter=impl_adapter,
        review_adapter=review_adapter,
        ticket_id=resolved_ticket_id,
        max_cycles=max_cycles,
        log_path=log_path,
        epic_id=epic_id,
    )

    # Reload ticket for gate/blocked reasons
    row = conn.execute(
        "SELECT gate_reason, blocked_reason FROM tickets WHERE id = ?",
        (resolved_ticket_id,),
    ).fetchone()

    return CommandResult(
        ticket_id=resolved_ticket_id,
        final_status=final_status,
        detail=detail,
        gate_reason=row["gate_reason"] if row else None,
        blocked_reason=row["blocked_reason"] if row else None,
    )
