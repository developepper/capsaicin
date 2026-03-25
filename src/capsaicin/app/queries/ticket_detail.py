"""Ticket detail read model — single-ticket view data."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field


@dataclass
class DiffSummary:
    """Compact diff information for the ticket detail view."""

    files_changed: list[str] = field(default_factory=list)
    diff_text: str | None = None


@dataclass
class RunDiagnosticSummary:
    """Structured diagnostic info for a run, extracted from adapter metadata."""

    cost_usd: float | None = None
    denial_summary: str | None = None
    agent_text: str | None = None


@dataclass
class TicketDetailData:
    """Structured ticket detail for operator views."""

    ticket: dict
    criteria: list[dict] = field(default_factory=list)
    open_findings: dict[str, list[dict]] = field(default_factory=dict)
    dependencies: list[dict] = field(default_factory=list)
    last_run: dict | None = None
    last_run_diagnostic: RunDiagnosticSummary | None = None
    last_run_evidence: list[dict] = field(default_factory=list)
    run_history: list[dict] | None = None
    transition_history: list[dict] | None = None
    diagnostic: str | None = None
    diff_summary: DiffSummary | None = None
    planned_ticket: dict | None = None


def _parse_adapter_metadata(raw: str | None) -> dict:
    """Parse adapter_metadata JSON, returning {} on any failure."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _build_run_diagnostic_summary(
    last_run: dict,
) -> RunDiagnosticSummary:
    """Build structured diagnostic info from the last run record."""
    from capsaicin.diagnostics import (
        denial_summary,
        extract_result_text_from_raw,
    )

    meta = _parse_adapter_metadata(last_run.get("adapter_metadata"))
    cost = meta.get("total_cost_usd")
    denial = denial_summary(meta) or None
    agent_text = extract_result_text_from_raw(last_run.get("raw_stdout")) or None

    return RunDiagnosticSummary(
        cost_usd=cost,
        denial_summary=denial,
        agent_text=agent_text,
    )


def _get_diff_summary(conn: sqlite3.Connection, ticket_id: str) -> DiffSummary | None:
    """Load the most recent diff for the ticket's last implementer run."""
    run_row = conn.execute(
        "SELECT id FROM agent_runs "
        "WHERE ticket_id = ? AND role = 'implementer' "
        "ORDER BY started_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if run_row is None:
        return None

    diff_row = conn.execute(
        "SELECT diff_text, files_changed FROM run_diffs WHERE run_id = ?",
        (run_row["id"],),
    ).fetchone()
    if diff_row is None:
        return None

    try:
        files = json.loads(diff_row["files_changed"])
    except (json.JSONDecodeError, TypeError):
        files = []

    return DiffSummary(
        files_changed=files,
        diff_text=diff_row["diff_text"] or None,
    )


def _load_planned_ticket(
    conn: sqlite3.Connection, planned_ticket_id: str
) -> dict | None:
    """Load planned ticket with its criteria for handoff context display."""
    row = conn.execute(
        "SELECT id, title, goal, scope, non_goals, implementation_notes, "
        "references_ "
        "FROM planned_tickets WHERE id = ?",
        (planned_ticket_id,),
    ).fetchone()
    if row is None:
        return None

    pt = dict(row)
    # Parse JSON list fields into Python lists.
    for key in ("scope", "non_goals", "implementation_notes", "references_"):
        try:
            pt[key] = json.loads(pt[key]) if pt[key] else []
        except (json.JSONDecodeError, TypeError):
            pt[key] = []

    criteria_rows = conn.execute(
        "SELECT description FROM planned_ticket_criteria "
        "WHERE planned_ticket_id = ? ORDER BY id",
        (planned_ticket_id,),
    ).fetchall()
    pt["acceptance_criteria"] = [r["description"] for r in criteria_rows]

    return pt


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

    from capsaicin.queries import load_evidence_for_run

    last_run = get_last_run(conn, ticket_id)
    diagnostic = None
    last_run_diag = None
    last_run_evidence: list[dict] = []
    if last_run:
        diagnostic = build_run_outcome_message(conn, ticket_id, last_run["id"])
        if not diagnostic:
            diagnostic = None
        last_run_diag = _build_run_diagnostic_summary(last_run)
        last_run_evidence = load_evidence_for_run(conn, last_run["id"])

    deps = conn.execute(
        "SELECT td.depends_on_id, t.title, t.status "
        "FROM ticket_dependencies td "
        "JOIN tickets t ON t.id = td.depends_on_id "
        "WHERE td.ticket_id = ? "
        "ORDER BY t.title",
        (ticket_id,),
    ).fetchall()

    data = TicketDetailData(
        ticket=ticket,
        criteria=get_ticket_criteria(conn, ticket_id),
        open_findings=get_open_findings_by_severity(conn, ticket_id),
        dependencies=[dict(d) for d in deps],
        last_run=last_run,
        last_run_diagnostic=last_run_diag,
        last_run_evidence=last_run_evidence,
        diagnostic=diagnostic,
        diff_summary=_get_diff_summary(conn, ticket_id),
    )

    if verbose:
        data.run_history = get_run_history(conn, ticket_id)
        data.transition_history = get_transition_history(conn, ticket_id)

    # Load planned ticket handoff context if linked.
    planned_id = ticket.get("planned_ticket_id")
    if planned_id:
        data.planned_ticket = _load_planned_ticket(conn, planned_id)

    return data
