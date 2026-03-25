"""HTMX partial routes for planning dashboard and epic detail sections."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.app.queries.planning_detail import get_planning_detail
from capsaicin.config import load_config
from capsaicin.errors import PlannedEpicNotFoundError
from capsaicin.resolver import get_overrides_for_epic, resolve_all_roles
from capsaicin.app.queries.planning_summary import (
    get_active_epics,
    get_approved_epics,
    get_blocked_epics,
    get_epic_counts_by_status,
    get_human_gate_epics,
    PlanningSummaryData,
)
from capsaicin.state_machine import PLANNING_STATUS_ORDER
from capsaicin.web.gate_display import get_epic_gate_display
from capsaicin.web.templating import templates


async def partial_planning_gate(request: Request) -> HTMLResponse:
    """Return the planning human-gate inbox section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = PlanningSummaryData(
        total_epics=0,
        human_gate_epics=get_human_gate_epics(conn, project_id),
    )

    return templates.TemplateResponse(
        request,
        "partials/planning_gate.html",
        {"data": data},
    )


async def partial_planning_active(request: Request) -> HTMLResponse:
    """Return the active epics section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = PlanningSummaryData(
        total_epics=0,
        active_epics=get_active_epics(conn, project_id),
    )

    return templates.TemplateResponse(
        request,
        "partials/planning_active.html",
        {"data": data},
    )


async def partial_planning_approved(request: Request) -> HTMLResponse:
    """Return the approved epics section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = PlanningSummaryData(
        total_epics=0,
        approved_epics=get_approved_epics(conn, project_id),
    )

    return templates.TemplateResponse(
        request,
        "partials/planning_approved.html",
        {"data": data},
    )


async def partial_planning_blocked(request: Request) -> HTMLResponse:
    """Return the blocked epics section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    data = PlanningSummaryData(
        total_epics=0,
        blocked_epics=get_blocked_epics(conn, project_id),
    )

    return templates.TemplateResponse(
        request,
        "partials/planning_blocked.html",
        {"data": data},
    )


async def partial_planning_queue(request: Request) -> HTMLResponse:
    """Return the planning queue overview section fragment."""
    conn = request.state.conn
    project_id = request.app.state.project_id

    counts = get_epic_counts_by_status(conn, project_id)
    data = PlanningSummaryData(
        total_epics=sum(counts.values()),
        counts_by_status=counts,
    )

    return templates.TemplateResponse(
        request,
        "partials/planning_queue.html",
        {"data": data, "status_order": PLANNING_STATUS_ORDER},
    )


async def partial_epic_content(request: Request) -> HTMLResponse:
    """Return the epic detail content fragment for SSE-triggered refresh."""
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

    gate_display = get_epic_gate_display(data.epic.get("gate_reason"))

    config = load_config(request.app.state.config_path)
    role_assignments = resolve_all_roles(config, conn=conn, epic_id=epic_id)
    overrides = get_overrides_for_epic(conn, epic_id)

    return templates.TemplateResponse(
        request,
        "partials/epic_content.html",
        {
            "data": data,
            "gate_display": gate_display,
            "role_assignments": role_assignments,
            "roles": ["planner", "planning_reviewer"],
            "overrides": overrides,
            "scope": "epic",
            "scope_id": epic_id,
            "override_roles": ["planner", "planning_reviewer"],
        },
    )
