"""Dashboard read model — project-level summary data."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field


@dataclass
class OrchestratorSummary:
    """Current orchestrator state for the dashboard."""

    status: str
    active_ticket_id: str | None = None
    active_run_id: str | None = None


@dataclass
class InboxSummary:
    """Lightweight inbox summary for dashboard display."""

    count: int = 0
    tickets: list[dict] = field(default_factory=list)


@dataclass
class WorkspaceTicketSummary:
    """Lightweight workspace state for a ticket in dashboard lists."""

    ticket_id: str
    isolation_mode: str  # "shared", "branch", "worktree", "none"
    status: str | None = None
    failure_reason: str | None = None


@dataclass
class DashboardData:
    """Structured project dashboard for operator views."""

    total_tickets: int
    counts_by_status: dict[str, int] = field(default_factory=dict)
    active_ticket: dict | None = None
    human_gate_tickets: list[dict] = field(default_factory=list)
    blocked_tickets: list[dict] = field(default_factory=list)
    next_runnable: dict | None = None
    orchestrator: OrchestratorSummary | None = None
    inbox: InboxSummary | None = None
    recent_runs: list[dict] = field(default_factory=list)
    workspace_summaries: dict[str, WorkspaceTicketSummary] = field(default_factory=dict)


def get_orchestrator_summary(
    conn: sqlite3.Connection, project_id: str
) -> OrchestratorSummary:
    """Load orchestrator state for the dashboard."""
    row = conn.execute(
        "SELECT status, active_ticket_id, active_run_id "
        "FROM orchestrator_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        return OrchestratorSummary(status="idle")
    return OrchestratorSummary(
        status=row["status"],
        active_ticket_id=row["active_ticket_id"],
        active_run_id=row["active_run_id"],
    )


def get_inbox_summary(conn: sqlite3.Connection, project_id: str) -> InboxSummary:
    """Build a lightweight inbox summary from human-gate tickets."""
    rows = conn.execute(
        "SELECT id, title, gate_reason, status_changed_at "
        "FROM tickets "
        "WHERE project_id = ? AND status = 'human-gate' "
        "ORDER BY status_changed_at",
        (project_id,),
    ).fetchall()
    return InboxSummary(
        count=len(rows),
        tickets=[dict(r) for r in rows],
    )


def get_recent_runs(
    conn: sqlite3.Connection, project_id: str, limit: int = 10
) -> list[dict]:
    """Return most recent agent runs across the project."""
    rows = conn.execute(
        "SELECT ar.id AS run_id, ar.ticket_id, t.title AS ticket_title, "
        "ar.role, ar.exit_status, ar.verdict, ar.duration_seconds, "
        "ar.started_at, ar.cycle_number, ar.attempt_number "
        "FROM agent_runs ar "
        "JOIN tickets t ON t.id = ar.ticket_id "
        "WHERE t.project_id = ? "
        "ORDER BY ar.started_at DESC "
        "LIMIT ?",
        (project_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


_log = logging.getLogger(__name__)


def collect_workspace_summaries(
    conn: sqlite3.Connection, config, tickets: list[dict]
) -> dict[str, WorkspaceTicketSummary]:
    """Build workspace summaries for a list of tickets.

    Uses the same ``workspace_status`` command service as the CLI so the
    dashboard reflects the exact isolation state that would affect execution.

    .. note::

       This issues one ``workspace_status`` call per ticket (N+1).  For
       dashboards with many tickets a single batch query would be more
       efficient.  Expected shape::

           SELECT w.ticket_id, w.status, w.failure_reason
           FROM workspaces w
           INNER JOIN (
               SELECT ticket_id, MAX(created_at) AS latest
               FROM workspaces
               WHERE ticket_id IN (?, ?, ...)
                 AND status NOT IN ('cleaned', 'failed')
               GROUP BY ticket_id
           ) latest ON w.ticket_id = latest.ticket_id
                   AND w.created_at = latest.latest

       This replaces the per-ticket ``workspace_status`` loop with one
       round-trip and avoids repeated git checks.
    """
    from capsaicin.app.commands.workspace_ops import workspace_status

    if not config.workspace.enabled:
        summaries: dict[str, WorkspaceTicketSummary] = {}
        for t in tickets:
            tid = t.get("id") or t.get("ticket_id", "")
            summaries[tid] = WorkspaceTicketSummary(
                ticket_id=tid,
                isolation_mode="shared",
            )
        return summaries

    summaries: dict[str, WorkspaceTicketSummary] = {}
    for t in tickets:
        tid = t.get("id") or t.get("ticket_id", "")
        try:
            ws = workspace_status(conn, config, tid)
            summaries[tid] = WorkspaceTicketSummary(
                ticket_id=tid,
                isolation_mode=ws.isolation_mode,
                status=ws.status,
                failure_reason=ws.failure_reason,
            )
        except Exception:
            _log.debug("Could not load workspace status for %s", tid, exc_info=True)
    return summaries


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
        orchestrator=get_orchestrator_summary(conn, project_id),
        inbox=get_inbox_summary(conn, project_id),
        recent_runs=get_recent_runs(conn, project_id),
    )
