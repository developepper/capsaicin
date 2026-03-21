"""Dashboard read model — project-level summary data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class DashboardData:
    """Structured project dashboard for operator views."""

    total_tickets: int
    counts_by_status: dict[str, int] = field(default_factory=dict)
    active_ticket: dict | None = None
    human_gate_tickets: list[dict] = field(default_factory=list)
    blocked_tickets: list[dict] = field(default_factory=list)
    next_runnable: dict | None = None


def get_dashboard(conn: sqlite3.Connection, project_id: str) -> DashboardData:
    """Build a structured dashboard summary for the project."""
    from capsaicin.ticket_status import (
        get_active_ticket,
        get_blocked_tickets,
        get_human_gate_tickets,
        get_next_runnable_ticket,
        get_ticket_counts_by_status,
    )

    counts = get_ticket_counts_by_status(conn, project_id)
    total = sum(counts.values())

    return DashboardData(
        total_tickets=total,
        counts_by_status=counts,
        active_ticket=get_active_ticket(conn, project_id),
        human_gate_tickets=get_human_gate_tickets(conn, project_id),
        blocked_tickets=get_blocked_tickets(conn, project_id),
        next_runnable=get_next_runnable_ticket(conn, project_id),
    )
