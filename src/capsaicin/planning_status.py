"""Planning status rendering module.

Renders planning summary and epic detail to stdout.  Consumes
structured data from ``app.queries.planning_summary`` and
``app.queries.planning_detail``.
"""

from __future__ import annotations

import sqlite3

# Canonical display order for planning statuses.
_STATUS_ORDER = [
    "new",
    "drafting",
    "in-review",
    "revise",
    "human-gate",
    "approved",
    "blocked",
]


def render_planning_summary(conn: sqlite3.Connection, project_id: str) -> str:
    """Build a CLI-formatted planning summary from the structured query layer."""
    from capsaicin.app.queries.planning_summary import get_planning_summary

    data = get_planning_summary(conn, project_id)

    lines: list[str] = []
    lines.append(
        f"Planning Summary ({data.total_epics} "
        f"epic{'s' if data.total_epics != 1 else ''})"
    )
    lines.append("")

    if data.counts_by_status:
        lines.append("Status Counts:")
        for s in _STATUS_ORDER:
            if s in data.counts_by_status:
                lines.append(f"  {s}: {data.counts_by_status[s]}")
    else:
        lines.append("  No planned epics")

    if data.active_epics:
        lines.append("")
        lines.append("Active Epics:")
        for e in data.active_epics:
            label = e.get("title") or _truncate(e["problem_statement"], 60)
            lines.append(f"  {e['id']}: {label} [{e['status']}]")

    if data.human_gate_epics:
        lines.append("")
        lines.append("Awaiting Human Gate:")
        for e in data.human_gate_epics:
            reason = e["gate_reason"] or "unknown"
            label = e.get("title") or _truncate(e["problem_statement"], 60)
            lines.append(f"  {e['id']}: {label} ({reason})")

    if data.blocked_epics:
        lines.append("")
        lines.append("Blocked:")
        for e in data.blocked_epics:
            reason = e["blocked_reason"] or "unknown"
            label = e.get("title") or _truncate(e["problem_statement"], 60)
            lines.append(f"  {e['id']}: {label} ({reason})")

    return "\n".join(lines)


def render_planning_detail(
    conn: sqlite3.Connection,
    epic_id: str,
    verbose: bool = False,
) -> str:
    """Build a CLI-formatted epic detail from the structured query layer."""
    from capsaicin.app.queries.planning_detail import get_planning_detail

    data = get_planning_detail(conn, epic_id, verbose=verbose)
    epic = data.epic

    lines: list[str] = []

    # Header
    lines.append(f"Epic: {epic['id']}")
    lines.append(f"  Status: {epic['status']}")
    if epic.get("title"):
        lines.append(f"  Title: {epic['title']}")
    lines.append(f"  Problem: {_truncate(epic['problem_statement'], 120)}")
    if epic.get("summary"):
        lines.append(f"  Summary: {epic['summary']}")
    lines.append(f"  Status Changed: {epic['status_changed_at']}")
    lines.append(
        f"  Cycle: {epic['current_cycle']} "
        f"(draft attempt: {epic['current_draft_attempt']}, "
        f"review attempt: {epic['current_review_attempt']})"
    )
    if epic.get("gate_reason"):
        lines.append(f"  Gate Reason: {epic['gate_reason']}")
    if epic.get("blocked_reason"):
        lines.append(f"  Blocked Reason: {epic['blocked_reason']}")

    # Planned tickets
    lines.append("")
    lines.append("Planned Tickets:")
    if data.planned_tickets:
        for pt in data.planned_tickets:
            lines.append(f"  {pt['sequence']}. {pt['title']}")
            criteria = data.ticket_criteria.get(pt["id"], [])
            if criteria:
                for c in criteria:
                    lines.append(f"     - {c['description']}")
    else:
        lines.append("  (none)")

    # Open findings
    lines.append("")
    lines.append("Open Findings:")
    if data.open_findings:
        for f in data.open_findings:
            ticket_ref = ""
            if f.get("planned_ticket_id"):
                ticket_ref = f" [ticket:{f['planned_ticket_id']}]"
            lines.append(
                f"  - [{f['severity']}] [{f['category']}]{ticket_ref} {f['description']}"
            )
    else:
        lines.append("  (none)")

    # Last run
    lines.append("")
    if data.last_run:
        duration = (
            f"{data.last_run['duration_seconds']:.1f}s"
            if data.last_run["duration_seconds"] is not None
            else "n/a"
        )
        verdict = data.last_run["verdict"] or "n/a"
        lines.append("Last Run:")
        lines.append(f"  Role: {data.last_run['role']}")
        lines.append(f"  Exit Status: {data.last_run['exit_status']}")
        lines.append(f"  Duration: {duration}")
        lines.append(f"  Verdict: {verdict}")
    else:
        lines.append("Last Run: (none)")

    # Verbose: transition history
    if verbose and data.transition_history is not None:
        lines.append("")
        lines.append("Transition History:")
        if data.transition_history:
            for t in data.transition_history:
                reason = f" ({t['reason']})" if t["reason"] else ""
                lines.append(
                    f"  [{t['created_at']}] {t['from_status']} -> {t['to_status']} "
                    f"by {t['triggered_by']}{reason}"
                )
        else:
            lines.append("  (none)")

    return "\n".join(lines)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, adding ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
