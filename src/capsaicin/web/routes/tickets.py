"""Ticket detail route."""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.app.queries.ticket_detail import WorkspaceSummary, get_ticket_detail
from capsaicin.config import load_config
from capsaicin.resolver import (
    get_overrides_for_ticket,
    lookup_epic_id_for_ticket,
    resolve_all_roles,
)
from capsaicin.web.gate_display import get_ticket_gate_display
from capsaicin.web.templating import templates

_log = logging.getLogger(__name__)


def _build_workspace_summary(conn, config, ticket_id: str) -> WorkspaceSummary | None:
    """Build workspace summary using the same command service as the CLI.

    Returns ``None`` when workspace state cannot be determined (e.g.
    ticket was deleted between queries) rather than letting the detail
    page crash.
    """
    from capsaicin.app.commands.workspace_ops import workspace_status

    from capsaicin.workspace import RecoveryAction, get_recovery_action

    try:
        ws = workspace_status(conn, config, ticket_id)
    except Exception:
        _log.debug("Could not load workspace status for %s", ticket_id, exc_info=True)
        return None

    # Consult the backend recovery map to determine the correct action.
    # Only offer "Recover Workspace" when the policy says retry or recreate.
    # For human_gate (e.g. cleanup_conflict), surface "awaiting operator action".
    recovery_action = get_recovery_action(ws.failure_reason)
    is_failed = ws.status == "failed" and ws.failure_reason is not None
    needs_recovery = is_failed and recovery_action in (
        RecoveryAction.retry,
        RecoveryAction.recreate,
    )
    needs_cleanup = ws.status in ("active", "failed") and ws.isolation_mode != "shared"
    awaiting_human = is_failed and recovery_action == RecoveryAction.human_gate

    return WorkspaceSummary(
        isolation_mode=ws.isolation_mode,
        status=ws.status,
        branch_name=ws.branch_name,
        worktree_path=ws.worktree_path,
        failure_reason=ws.failure_reason,
        failure_detail=ws.failure_detail,
        needs_recovery=needs_recovery,
        needs_cleanup=needs_cleanup,
        awaiting_human=awaiting_human,
    )


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

    # Workspace isolation state (AC-1: surface in detail view)
    data.workspace = _build_workspace_summary(conn, config, ticket_id)

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
