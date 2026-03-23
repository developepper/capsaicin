"""Planning detail read model — single-epic view data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class PlanningDetailData:
    """Structured epic detail for operator views."""

    epic: dict
    planned_tickets: list[dict] = field(default_factory=list)
    ticket_criteria: dict[str, list[dict]] = field(default_factory=dict)
    open_findings: list[dict] = field(default_factory=list)
    last_run: dict | None = None
    transition_history: list[dict] | None = None


def get_planning_detail(
    conn: sqlite3.Connection,
    epic_id: str,
    verbose: bool = False,
) -> PlanningDetailData:
    """Build structured epic detail data.

    Raises ``PlannedEpicNotFoundError`` if the epic does not exist.
    """
    from capsaicin.queries import (
        load_open_planning_findings,
        load_planned_epic,
        load_planned_ticket_criteria,
        load_planned_tickets,
    )

    epic = load_planned_epic(conn, epic_id)
    planned_tickets = load_planned_tickets(conn, epic_id)

    ticket_criteria: dict[str, list[dict]] = {}
    for pt in planned_tickets:
        ticket_criteria[pt["id"]] = load_planned_ticket_criteria(conn, pt["id"])

    open_findings = load_open_planning_findings(conn, epic_id)

    last_run = conn.execute(
        "SELECT id, role, exit_status, duration_seconds, verdict, "
        "started_at, finished_at, cycle_number, attempt_number "
        "FROM agent_runs WHERE epic_id = ? "
        "ORDER BY started_at DESC LIMIT 1",
        (epic_id,),
    ).fetchone()
    last_run = dict(last_run) if last_run else None

    data = PlanningDetailData(
        epic=epic,
        planned_tickets=planned_tickets,
        ticket_criteria=ticket_criteria,
        open_findings=open_findings,
        last_run=last_run,
    )

    if verbose:
        rows = conn.execute(
            "SELECT from_status, to_status, triggered_by, reason, created_at "
            "FROM state_transitions WHERE epic_id = ? "
            "ORDER BY created_at",
            (epic_id,),
        ).fetchall()
        data.transition_history = [dict(r) for r in rows]

    return data
