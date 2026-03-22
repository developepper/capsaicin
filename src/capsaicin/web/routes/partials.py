"""HTMX partial routes for dashboard and ticket-detail sections.

Each endpoint builds a minimal data object containing only the fields
its template needs, so partial refreshes don't pay for a full query.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse

from capsaicin.app.queries.dashboard import (
    DashboardData,
    get_inbox_summary,
    get_orchestrator_summary,
    get_recent_runs,
)
from capsaicin.app.queries.ticket_detail import get_ticket_detail
from capsaicin.ticket_status import (
    get_active_ticket,
    get_blocked_tickets,
    get_next_runnable_ticket,
    get_ticket_counts_by_status,
)
from capsaicin.web.templating import templates


async def partial_inbox(request: Request) -> HTMLResponse:
    """Return the inbox section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = DashboardData(
        total_tickets=0,
        inbox=get_inbox_summary(conn, project_id),
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
        {"data": data},
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

    data = DashboardData(
        total_tickets=0,
        blocked_tickets=get_blocked_tickets(conn, project_id),
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

    data = DashboardData(
        total_tickets=0,
        orchestrator=get_orchestrator_summary(conn, project_id),
        active_ticket=get_active_ticket(conn, project_id),
    )

    return templates.TemplateResponse(
        request,
        "partials/orchestrator.html",
        {"data": data},
    )


async def partial_ticket_content(request: Request) -> HTMLResponse:
    """Return the ticket detail content fragment for SSE-triggered refresh."""
    conn = request.state.conn
    ticket_id = request.path_params["ticket_id"]

    try:
        data = get_ticket_detail(conn, ticket_id, verbose=True)
    except ValueError:
        return PlainTextResponse(f"Ticket '{ticket_id}' not found.", status_code=404)

    return templates.TemplateResponse(
        request,
        "partials/ticket_content.html",
        {"data": data},
    )
