"""Planning dashboard and epic detail routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.app.queries.planning_detail import get_planning_detail
from capsaicin.app.queries.planning_summary import get_planning_summary
from capsaicin.errors import PlannedEpicNotFoundError
from capsaicin.state_machine import PLANNING_STATUS_ORDER
from capsaicin.web.gate_display import get_epic_gate_display
from capsaicin.web.templating import templates


async def planning_dashboard(request: Request) -> HTMLResponse:
    """Render the planning overview dashboard."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = get_planning_summary(conn, project_id)
    error = request.query_params.get("error")

    return templates.TemplateResponse(
        request,
        "planning_dashboard.html",
        {"data": data, "error": error, "status_order": PLANNING_STATUS_ORDER},
    )


async def epic_detail(request: Request) -> HTMLResponse:
    """Render a single epic's detail view."""
    conn = request.state.conn
    epic_id = request.path_params["epic_id"]

    try:
        data = get_planning_detail(conn, epic_id, verbose=True)
    except (ValueError, LookupError, PlannedEpicNotFoundError):
        return templates.TemplateResponse(
            request,
            "404.html",
            {"message": f"Epic '{epic_id}' not found."},
            status_code=404,
        )

    error = request.query_params.get("error")
    gate_display = get_epic_gate_display(data.epic.get("gate_reason"))

    return templates.TemplateResponse(
        request,
        "epic_detail.html",
        {"data": data, "error": error, "gate_display": gate_display},
    )
