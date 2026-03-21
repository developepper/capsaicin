"""Ticket detail read model — single-ticket view data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class TicketDetailData:
    """Structured ticket detail for operator views."""

    ticket: dict
    criteria: list[dict] = field(default_factory=list)
    open_findings: dict[str, list[dict]] = field(default_factory=dict)
    last_run: dict | None = None
    run_history: list[dict] | None = None
    transition_history: list[dict] | None = None
    diagnostic: str | None = None


def get_ticket_detail(
    conn: sqlite3.Connection,
    ticket_id: str,
    verbose: bool = False,
) -> TicketDetailData:
    """Build structured ticket detail data.

    Raises ``ValueError`` if the ticket does not exist.
    """
    from capsaicin.diagnostics import build_run_outcome_message
    from capsaicin.ticket_status import (
        get_last_run,
        get_open_findings_by_severity,
        get_run_history,
        get_ticket_criteria,
        get_ticket_detail as _get_ticket_row,
        get_transition_history,
    )

    ticket = _get_ticket_row(conn, ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")

    last_run = get_last_run(conn, ticket_id)
    diagnostic = None
    if last_run:
        diagnostic = build_run_outcome_message(conn, ticket_id, last_run["id"])
        if not diagnostic:
            diagnostic = None

    data = TicketDetailData(
        ticket=ticket,
        criteria=get_ticket_criteria(conn, ticket_id),
        open_findings=get_open_findings_by_severity(conn, ticket_id),
        last_run=last_run,
        diagnostic=diagnostic,
    )

    if verbose:
        data.run_history = get_run_history(conn, ticket_id)
        data.transition_history = get_transition_history(conn, ticket_id)

    return data
