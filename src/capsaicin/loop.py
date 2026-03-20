"""Automated implement-review-revise loop for ``capsaicin loop`` (T27).

Executes the T15 and T20 pipelines in-process, looping until the ticket
reaches ``human-gate`` or ``blocked``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.adapters.base import BaseAdapter
from capsaicin.config import Config
from capsaicin.resume import build_human_gate_context
from capsaicin.ticket_review import run_review_pipeline, select_review_ticket
from capsaicin.ticket_run import run_implementation_pipeline, select_ticket


def _reload_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict:
    """Reload ticket from DB to get fresh status."""
    row = conn.execute(
        "SELECT id, project_id, title, description, status, "
        "current_cycle, current_impl_attempt, current_review_attempt, "
        "gate_reason, blocked_reason "
        "FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")
    return dict(row)


def run_loop(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    impl_adapter: BaseAdapter,
    review_adapter: BaseAdapter,
    ticket_id: str | None = None,
    max_cycles: int | None = None,
    log_path: str | Path | None = None,
) -> tuple[str, str]:
    """Run the implement-review-revise loop.

    Returns a tuple of (final_status, detail) describing the outcome.
    """
    if max_cycles is not None:
        config.limits.max_cycles = max_cycles

    # Select ticket (must be in ready or revise)
    ticket = select_ticket(conn, ticket_id)
    tid = ticket["id"]

    if log_path:
        log_event(
            log_path,
            "LOOP_START",
            project_id=project_id,
            ticket_id=tid,
            payload={"max_cycles": config.limits.max_cycles},
        )

    while True:
        ticket = _reload_ticket(conn, tid)
        status = ticket["status"]

        if status in ("ready", "revise"):
            # --- Implementation phase ---
            run_implementation_pipeline(
                conn=conn,
                project_id=project_id,
                ticket=ticket,
                config=config,
                adapter=impl_adapter,
                log_path=log_path,
            )
            # Reload and continue — next iteration handles the result
            continue

        if status == "in-review":
            # --- Review phase ---
            review_ticket = select_review_ticket(conn, tid)
            run_review_pipeline(
                conn=conn,
                project_id=project_id,
                ticket=review_ticket,
                config=config,
                adapter=review_adapter,
                log_path=log_path,
            )
            # Reload and continue — next iteration handles the result
            continue

        # --- Terminal states ---
        if status == "human-gate":
            context = build_human_gate_context(conn, tid)
            if log_path:
                log_event(
                    log_path,
                    "LOOP_STOP",
                    project_id=project_id,
                    ticket_id=tid,
                    payload={
                        "reason": "human-gate",
                        "gate_reason": ticket["gate_reason"],
                    },
                )
            return ("human-gate", context)

        if status == "blocked":
            detail = (
                f"Ticket {tid} is blocked.\n"
                f"  Blocked Reason: {ticket['blocked_reason'] or 'unknown'}"
            )
            if log_path:
                log_event(
                    log_path,
                    "LOOP_STOP",
                    project_id=project_id,
                    ticket_id=tid,
                    payload={
                        "reason": "blocked",
                        "blocked_reason": ticket["blocked_reason"],
                    },
                )
            return ("blocked", detail)

        if status in ("pr-ready", "done"):
            return (status, f"Ticket {tid} is already {status}.")

        # Unexpected status — bail
        return (
            status,
            f"Ticket {tid} is in unexpected status '{status}' for loop.",
        )
