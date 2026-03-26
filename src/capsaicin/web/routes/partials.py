"""HTMX partial routes for dashboard and ticket-detail sections.

Each endpoint builds a minimal data object containing only the fields
its template needs, so partial refreshes don't pay for a full query.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.app.queries.dashboard import (
    DashboardData,
    collect_workspace_summaries,
    get_inbox_summary,
    get_orchestrator_summary,
    get_recent_runs,
)
from capsaicin.app.queries.ticket_detail import get_ticket_detail
from capsaicin.config import load_config
from capsaicin.resolver import (
    get_overrides_for_ticket,
    lookup_epic_id_for_ticket,
    resolve_all_roles,
)
from capsaicin.state_machine import TICKET_STATUS_ORDER
from capsaicin.ticket_status import (
    get_active_ticket,
    get_blocked_tickets,
    get_next_runnable_ticket,
    get_ticket_counts_by_status,
)
from capsaicin.web.gate_display import get_ticket_gate_display
from capsaicin.web.templating import templates


async def partial_inbox(request: Request) -> HTMLResponse:
    """Return the inbox section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    inbox = get_inbox_summary(conn, project_id)
    config = load_config(request.app.state.config_path)

    data = DashboardData(
        total_tickets=0,
        inbox=inbox,
        workspace_summaries=collect_workspace_summaries(conn, config, inbox.tickets),
    )

    return templates.TemplateResponse(
        request,
        "partials/inbox.html",
        {"data": data},
    )


async def partial_queue(request: Request) -> HTMLResponse:
    """Return the queue section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    counts = get_ticket_counts_by_status(conn, project_id)
    data = DashboardData(
        total_tickets=sum(counts.values()),
        counts_by_status=counts,
    )

    return templates.TemplateResponse(
        request,
        "partials/queue.html",
        {"data": data, "status_order": TICKET_STATUS_ORDER},
    )


async def partial_activity(request: Request) -> HTMLResponse:
    """Return the recent activity section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = DashboardData(
        total_tickets=0,
        recent_runs=get_recent_runs(conn, project_id),
    )

    return templates.TemplateResponse(
        request,
        "partials/activity.html",
        {"data": data},
    )


async def partial_blocked(request: Request) -> HTMLResponse:
    """Return the blocked tickets section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    blocked = get_blocked_tickets(conn, project_id)
    config = load_config(request.app.state.config_path)

    data = DashboardData(
        total_tickets=0,
        blocked_tickets=blocked,
        workspace_summaries=collect_workspace_summaries(conn, config, blocked),
    )

    return templates.TemplateResponse(
        request,
        "partials/blocked.html",
        {"data": data},
    )


async def partial_next_runnable(request: Request) -> HTMLResponse:
    """Return the next-runnable ticket section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = DashboardData(
        total_tickets=0,
        next_runnable=get_next_runnable_ticket(conn, project_id),
    )

    return templates.TemplateResponse(
        request,
        "partials/next_runnable.html",
        {"data": data},
    )


async def partial_orchestrator(request: Request) -> HTMLResponse:
    """Return the orchestrator bar fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    active = get_active_ticket(conn, project_id)
    config = load_config(request.app.state.config_path)
    relevant = [active] if active else []

    data = DashboardData(
        total_tickets=0,
        orchestrator=get_orchestrator_summary(conn, project_id),
        active_ticket=active,
        workspace_summaries=collect_workspace_summaries(conn, config, relevant),
    )

    return templates.TemplateResponse(
        request,
        "partials/orchestrator.html",
        {"data": data},
    )


async def partial_ticket_content(request: Request) -> HTMLResponse:
    """Return the ticket detail content fragment for SSE-triggered refresh."""
    from capsaicin.web.routes.tickets import _build_workspace_summary

    conn = request.state.conn
    ticket_id = request.path_params["ticket_id"]

    try:
        data = get_ticket_detail(conn, ticket_id, verbose=True)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "404.html",
            {"message": f"Ticket '{ticket_id}' not found."},
            status_code=404,
        )

    gate_display = get_ticket_gate_display(data.ticket.get("gate_reason"))

    config = load_config(request.app.state.config_path)
    epic_id = lookup_epic_id_for_ticket(conn, ticket_id)
    role_assignments = resolve_all_roles(
        config, conn=conn, ticket_id=ticket_id, epic_id=epic_id
    )
    overrides = get_overrides_for_ticket(conn, ticket_id)

    data.workspace = _build_workspace_summary(conn, config, ticket_id)

    return templates.TemplateResponse(
        request,
        "partials/ticket_content.html",
        {
            "data": data,
            "gate_display": gate_display,
            "role_assignments": role_assignments,
            "roles": ["implementer", "reviewer"],
            "overrides": overrides,
            "scope": "ticket",
            "scope_id": ticket_id,
            "override_roles": ["implementer", "reviewer"],
        },
    )
