"""Ticket detail route."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse

from capsaicin.app.queries.ticket_detail import get_ticket_detail
from capsaicin.web.gate_display import get_ticket_gate_display
from capsaicin.web.templating import templates


async def ticket_detail(request: Request) -> HTMLResponse:
    """Render a single ticket's detail view."""
    conn = request.state.conn
    ticket_id = request.path_params["ticket_id"]

    try:
        data = get_ticket_detail(conn, ticket_id, verbose=True)
    except ValueError:
        return PlainTextResponse(f"Ticket '{ticket_id}' not found.", status_code=404)

    error = request.query_params.get("error")
    gate_display = get_ticket_gate_display(data.ticket.get("gate_reason"))

    return templates.TemplateResponse(
        request,
        "ticket_detail.html",
        {"data": data, "error": error, "gate_display": gate_display},
    )
