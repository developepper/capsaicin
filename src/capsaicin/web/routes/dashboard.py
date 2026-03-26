"""Dashboard route — project overview."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.app.queries.dashboard import collect_workspace_summaries, get_dashboard
from capsaicin.config import load_config
from capsaicin.state_machine import TICKET_STATUS_ORDER
from capsaicin.web.templating import templates


async def dashboard(request: Request) -> HTMLResponse:
    """Render the project dashboard."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = get_dashboard(conn, project_id)
    error = request.query_params.get("error")

    # Annotate blocked tickets with workspace state (AC-1)
    config = load_config(request.app.state.config_path)
    all_relevant = list(data.blocked_tickets)
    if data.active_ticket:
        all_relevant.append(data.active_ticket)
    if data.inbox:
        all_relevant.extend(data.inbox.tickets)
    data.workspace_summaries = collect_workspace_summaries(conn, config, all_relevant)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"data": data, "error": error, "status_order": TICKET_STATUS_ORDER},
    )
