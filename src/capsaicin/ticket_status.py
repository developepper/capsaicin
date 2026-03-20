"""Status rendering module (T25).

Renders project summary and ticket detail to stdout.
"""

from __future__ import annotations

import sqlite3


def get_ticket_counts_by_status(conn: sqlite3.Connection, project_id: str) -> dict:
    """Return ticket counts grouped by status."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM tickets "
        "WHERE project_id = ? GROUP BY status",
        (project_id,),
    ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}


def get_active_ticket(conn: sqlite3.Connection, project_id: str) -> dict | None:
    """Return the active ticket from orchestrator_state, or None."""
    row = conn.execute(
        "SELECT active_ticket_id FROM orchestrator_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None or row["active_ticket_id"] is None:
        return None
    ticket = conn.execute(
        "SELECT id, title, status FROM tickets WHERE id = ?",
        (row["active_ticket_id"],),
    ).fetchone()
    if ticket is None:
        return None
    return dict(ticket)


def get_human_gate_tickets(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Return tickets in human-gate status with gate_reason."""
    rows = conn.execute(
        "SELECT id, title, gate_reason FROM tickets "
        "WHERE project_id = ? AND status = 'human-gate' "
        "ORDER BY status_changed_at",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_blocked_tickets(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Return blocked tickets with blocked_reason."""
    rows = conn.execute(
        "SELECT id, title, blocked_reason FROM tickets "
        "WHERE project_id = ? AND status = 'blocked' "
        "ORDER BY status_changed_at",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_next_runnable_ticket(conn: sqlite3.Connection, project_id: str) -> dict | None:
    """Return the next ready ticket whose dependencies are all done."""
    ready_tickets = conn.execute(
        "SELECT id, title FROM tickets "
        "WHERE project_id = ? AND status = 'ready' "
        "ORDER BY created_at",
        (project_id,),
    ).fetchall()

    for ticket in ready_tickets:
        deps = conn.execute(
            "SELECT t.status FROM ticket_dependencies td "
            "JOIN tickets t ON t.id = td.depends_on_id "
            "WHERE td.ticket_id = ?",
            (ticket["id"],),
        ).fetchall()
        if all(d["status"] == "done" for d in deps):
            return dict(ticket)

    return None


def build_project_summary(conn: sqlite3.Connection, project_id: str) -> str:
    """Build a project summary string for stdout."""
    lines: list[str] = []

    # Ticket counts by status
    counts = get_ticket_counts_by_status(conn, project_id)
    total = sum(counts.values())
    lines.append(f"Project Summary ({total} ticket{'s' if total != 1 else ''})")
    lines.append("")

    if counts:
        lines.append("Status Counts:")
        for status in [
            "ready",
            "implementing",
            "in-review",
            "revise",
            "human-gate",
            "pr-ready",
            "blocked",
            "done",
        ]:
            if status in counts:
                lines.append(f"  {status}: {counts[status]}")
    else:
        lines.append("  No tickets")

    # Active ticket
    lines.append("")
    active = get_active_ticket(conn, project_id)
    if active:
        lines.append(f"Active Ticket: {active['id']}")
        lines.append(f"  Title: {active['title']}")
        lines.append(f"  Status: {active['status']}")
    else:
        lines.append("Active Ticket: (none)")

    # Human-gate tickets
    gate_tickets = get_human_gate_tickets(conn, project_id)
    if gate_tickets:
        lines.append("")
        lines.append("Awaiting Human Gate:")
        for t in gate_tickets:
            reason = t["gate_reason"] or "unknown"
            lines.append(f"  {t['id']}: {t['title']} ({reason})")

    # Blocked tickets
    blocked = get_blocked_tickets(conn, project_id)
    if blocked:
        lines.append("")
        lines.append("Blocked:")
        for t in blocked:
            reason = t["blocked_reason"] or "unknown"
            lines.append(f"  {t['id']}: {t['title']} ({reason})")

    # Next runnable ticket
    lines.append("")
    next_ticket = get_next_runnable_ticket(conn, project_id)
    if next_ticket:
        lines.append(f"Next Runnable: {next_ticket['id']}: {next_ticket['title']}")
    else:
        lines.append("Next Runnable: (none)")

    return "\n".join(lines)


def get_ticket_detail(conn: sqlite3.Connection, ticket_id: str) -> dict | None:
    """Return full ticket detail for display."""
    row = conn.execute(
        "SELECT id, title, description, status, status_changed_at, "
        "current_cycle, current_impl_attempt, current_review_attempt, "
        "gate_reason, blocked_reason, created_at "
        "FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_ticket_criteria(conn: sqlite3.Connection, ticket_id: str) -> list[dict]:
    """Return acceptance criteria for a ticket."""
    rows = conn.execute(
        "SELECT id, description, status FROM acceptance_criteria "
        "WHERE ticket_id = ? ORDER BY id",
        (ticket_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_open_findings_by_severity(
    conn: sqlite3.Connection, ticket_id: str
) -> dict[str, list[dict]]:
    """Return open findings grouped by severity."""
    rows = conn.execute(
        "SELECT id, severity, category, location, description "
        "FROM findings WHERE ticket_id = ? AND disposition = 'open' "
        "ORDER BY severity, created_at",
        (ticket_id,),
    ).fetchall()

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        sev = r["severity"]
        if sev not in grouped:
            grouped[sev] = []
        grouped[sev].append(dict(r))
    return grouped


def get_last_run(conn: sqlite3.Connection, ticket_id: str) -> dict | None:
    """Return the most recent agent run for a ticket."""
    row = conn.execute(
        "SELECT id, role, exit_status, duration_seconds, verdict, "
        "started_at, finished_at "
        "FROM agent_runs WHERE ticket_id = ? "
        "ORDER BY started_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_run_history(conn: sqlite3.Connection, ticket_id: str) -> list[dict]:
    """Return all runs for a ticket, ordered by start time."""
    rows = conn.execute(
        "SELECT id, role, exit_status, duration_seconds, verdict, "
        "cycle_number, attempt_number, started_at, finished_at "
        "FROM agent_runs WHERE ticket_id = ? "
        "ORDER BY started_at",
        (ticket_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_transition_history(conn: sqlite3.Connection, ticket_id: str) -> list[dict]:
    """Return all state transitions for a ticket, ordered by time."""
    rows = conn.execute(
        "SELECT from_status, to_status, triggered_by, reason, created_at "
        "FROM state_transitions WHERE ticket_id = ? "
        "ORDER BY created_at",
        (ticket_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def build_ticket_detail(
    conn: sqlite3.Connection, ticket_id: str, verbose: bool = False
) -> str:
    """Build a ticket detail string for stdout."""
    ticket = get_ticket_detail(conn, ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")

    lines: list[str] = []

    # Header
    lines.append(f"Ticket: {ticket['id']}")
    lines.append(f"  Title: {ticket['title']}")
    lines.append(f"  Status: {ticket['status']}")
    lines.append(f"  Status Changed: {ticket['status_changed_at']}")
    lines.append(
        f"  Cycle: {ticket['current_cycle']} "
        f"(impl attempt: {ticket['current_impl_attempt']}, "
        f"review attempt: {ticket['current_review_attempt']})"
    )
    if ticket["gate_reason"]:
        lines.append(f"  Gate Reason: {ticket['gate_reason']}")
    if ticket["blocked_reason"]:
        lines.append(f"  Blocked Reason: {ticket['blocked_reason']}")

    # Acceptance criteria
    lines.append("")
    lines.append("Acceptance Criteria:")
    criteria = get_ticket_criteria(conn, ticket_id)
    if criteria:
        for c in criteria:
            lines.append(f"  [{c['status']}] {c['description']}")
    else:
        lines.append("  (none)")

    # Open findings grouped by severity
    lines.append("")
    lines.append("Open Findings:")
    findings = get_open_findings_by_severity(conn, ticket_id)
    if findings:
        for severity in ["blocking", "warning", "info"]:
            if severity in findings:
                lines.append(f"  {severity}:")
                for f in findings[severity]:
                    loc = f" ({f['location']})" if f["location"] else ""
                    lines.append(f"    - [{f['category']}]{loc} {f['description']}")
    else:
        lines.append("  (none)")

    # Last run summary
    lines.append("")
    last_run = get_last_run(conn, ticket_id)
    if last_run:
        duration = (
            f"{last_run['duration_seconds']:.1f}s"
            if last_run["duration_seconds"] is not None
            else "n/a"
        )
        verdict = last_run["verdict"] or "n/a"
        lines.append("Last Run:")
        lines.append(f"  Role: {last_run['role']}")
        lines.append(f"  Exit Status: {last_run['exit_status']}")
        lines.append(f"  Duration: {duration}")
        lines.append(f"  Verdict: {verdict}")
    else:
        lines.append("Last Run: (none)")

    # Verbose: run history and transition history
    if verbose:
        lines.append("")
        lines.append("Run History:")
        runs = get_run_history(conn, ticket_id)
        if runs:
            for r in runs:
                duration = (
                    f"{r['duration_seconds']:.1f}s"
                    if r["duration_seconds"] is not None
                    else "n/a"
                )
                verdict = r["verdict"] or "n/a"
                lines.append(
                    f"  [{r['started_at']}] {r['role']} "
                    f"cycle={r['cycle_number']} attempt={r['attempt_number']} "
                    f"exit={r['exit_status']} verdict={verdict} "
                    f"duration={duration}"
                )
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("Transition History:")
        transitions = get_transition_history(conn, ticket_id)
        if transitions:
            for t in transitions:
                reason = f" ({t['reason']})" if t["reason"] else ""
                lines.append(
                    f"  [{t['created_at']}] {t['from_status']} -> {t['to_status']} "
                    f"by {t['triggered_by']}{reason}"
                )
        else:
            lines.append("  (none)")

    return "\n".join(lines)
