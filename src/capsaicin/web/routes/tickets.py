"""Ticket detail route."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.app.queries.ticket_detail import get_ticket_detail
from capsaicin.config import load_config
from capsaicin.resolver import (
    get_overrides_for_ticket,
    lookup_epic_id_for_ticket,
    resolve_all_roles,
)
from capsaicin.web.gate_display import get_ticket_gate_display
from capsaicin.web.templating import templates


async def ticket_detail(request: Request) -> HTMLResponse:
    """Render a single ticket's detail view."""
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

    error = request.query_params.get("error")
    gate_display = get_ticket_gate_display(data.ticket.get("gate_reason"))

    # Resolve role assignments for implementer/reviewer display
    config = load_config(request.app.state.config_path)
    epic_id = lookup_epic_id_for_ticket(conn, ticket_id)
    role_assignments = resolve_all_roles(
        config, conn=conn, ticket_id=ticket_id, epic_id=epic_id
    )
    overrides = get_overrides_for_ticket(conn, ticket_id)

    return templates.TemplateResponse(
        request,
        "ticket_detail.html",
        {
            "data": data,
            "error": error,
            "gate_display": gate_display,
            "role_assignments": role_assignments,
            "roles": ["implementer", "reviewer"],
            "overrides": overrides,
            "scope": "ticket",
            "scope_id": ticket_id,
            "override_roles": ["implementer", "reviewer"],
        },
    )
