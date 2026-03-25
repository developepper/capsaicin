"""Planning detail read model — single-epic view data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from capsaicin.adapters.types import BackendEvidence, EvidenceRequirement


@dataclass
class PlanningDetailData:
    """Structured epic detail for operator views."""

    epic: dict
    planned_tickets: list[dict] = field(default_factory=list)
    ticket_criteria: dict[str, list[dict]] = field(default_factory=dict)
    open_findings: list[dict] = field(default_factory=list)
    impl_tickets: list[dict] = field(default_factory=list)
    last_run: dict | None = None
    last_run_evidence: list[dict] = field(default_factory=list)
    transition_history: list[dict] | None = None
    evidence: list[BackendEvidence] = field(default_factory=list)
    evidence_requirements: list[EvidenceRequirement] = field(default_factory=list)
    evidence_consumers: dict[str, list[dict]] = field(default_factory=dict)
    evidence_timeline: list[dict] = field(default_factory=list)


def _load_impl_tickets(
    conn: sqlite3.Connection,
    epic_id: str,
) -> list[dict]:
    """Load materialized implementation tickets for an epic with dependency info."""
    rows = conn.execute(
        "SELECT t.id, t.title, t.status, t.planned_ticket_id, pt.sequence "
        "FROM tickets t "
        "JOIN planned_tickets pt ON pt.id = t.planned_ticket_id "
        "WHERE pt.epic_id = ? "
        "ORDER BY pt.sequence",
        (epic_id,),
    ).fetchall()
    if not rows:
        return []

    ticket_ids = [r["id"] for r in rows]
    placeholders = ", ".join("?" for _ in ticket_ids)

    # Load all dependencies for these tickets in one query
    dep_rows = conn.execute(
        f"SELECT td.ticket_id, td.depends_on_id, t.status AS dep_status "
        f"FROM ticket_dependencies td "
        f"JOIN tickets t ON t.id = td.depends_on_id "
        f"WHERE td.ticket_id IN ({placeholders})",
        ticket_ids,
    ).fetchall()

    # Group dependencies by ticket_id
    deps_by_ticket: dict[str, list[dict]] = {}
    for dep in dep_rows:
        deps_by_ticket.setdefault(dep["ticket_id"], []).append(
            {"depends_on_id": dep["depends_on_id"], "status": dep["dep_status"]}
        )

    result = []
    for r in rows:
        deps = deps_by_ticket.get(r["id"], [])
        is_ready = all(d["status"] == "done" for d in deps) if deps else True
        result.append(
            {
                "id": r["id"],
                "title": r["title"],
                "status": r["status"],
                "planned_ticket_id": r["planned_ticket_id"],
                "sequence": r["sequence"],
                "dependencies": deps,
                "is_ready": is_ready,
            }
        )
    return result


def get_planning_detail(
    conn: sqlite3.Connection,
    epic_id: str,
    verbose: bool = False,
) -> PlanningDetailData:
    """Build structured epic detail data.

    Raises ``PlannedEpicNotFoundError`` if the epic does not exist.
    """
    from capsaicin.queries import (
        load_backend_evidence_for_epic,
        load_evidence_for_run,
        load_evidence_requirements_for_epic,
        load_evidence_timeline,
        load_open_planning_findings,
        load_planned_epic,
        load_planned_ticket_criteria,
        load_planned_tickets,
        load_runs_for_evidence,
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

    impl_tickets = _load_impl_tickets(conn, epic_id)

    evidence = load_backend_evidence_for_epic(conn, epic_id)
    evidence_requirements = load_evidence_requirements_for_epic(conn, epic_id)

    # Load evidence consumed by last run (T09)
    last_run_evidence: list[dict] = []
    if last_run:
        last_run_evidence = load_evidence_for_run(conn, last_run["id"])

    # Load which runs consumed each evidence record (T09)
    evidence_consumers: dict[str, list[dict]] = {}
    for ev in evidence:
        consumers = load_runs_for_evidence(conn, ev.id)
        if consumers:
            evidence_consumers[ev.id] = consumers

    # Load evidence timeline (T09)
    evidence_timeline = load_evidence_timeline(conn, epic_id)

    data = PlanningDetailData(
        epic=epic,
        planned_tickets=planned_tickets,
        ticket_criteria=ticket_criteria,
        open_findings=open_findings,
        impl_tickets=impl_tickets,
        last_run=last_run,
        last_run_evidence=last_run_evidence,
        evidence=evidence,
        evidence_requirements=evidence_requirements,
        evidence_consumers=evidence_consumers,
        evidence_timeline=evidence_timeline,
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
