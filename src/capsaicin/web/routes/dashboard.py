"""Dashboard route — project overview."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.app.queries.dashboard import get_dashboard
from capsaicin.web.templating import templates


async def dashboard(request: Request) -> HTMLResponse:
    """Render the project dashboard."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = get_dashboard(conn, project_id)
    error = request.query_params.get("error")

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"data": data, "error": error},
    )
