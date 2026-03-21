"""HTMX partial routes for dashboard sections.

Each endpoint builds a minimal DashboardData containing only the fields
its template needs, so partial refreshes don't pay for a full dashboard
query.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.app.queries.dashboard import (
    DashboardData,
    get_inbox_summary,
    get_recent_runs,
)
from capsaicin.ticket_status import get_ticket_counts_by_status
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
