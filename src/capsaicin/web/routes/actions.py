"""POST action routes for human-gate decisions and workflow triggers.

Each handler delegates to the shared command services from ``app.commands``
and redirects back to the ticket detail or dashboard view.  This keeps
action semantics identical to the CLI — the web UI does not reinterpret
state-machine rules.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, HTMLResponse

from capsaicin.config import load_config
from capsaicin.errors import CapsaicinError
from capsaicin.web.templating import templates


# ---------------------------------------------------------------------------
# Human-gate actions
# ---------------------------------------------------------------------------


async def action_approve(request: Request) -> RedirectResponse | HTMLResponse:
    """POST /tickets/{ticket_id}/approve — approve a human-gate ticket."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    form = await request.form()
    rationale = form.get("rationale", "").strip() or None
    force = form.get("force") == "on"

    from capsaicin.app.commands.approve_ticket import approve
    from capsaicin.ticket_approve import WorkspaceMismatchError

    try:
        approve(
            conn=conn,
            project_id=project_id,
            config=config,
            ticket_id=ticket_id,
            rationale=rationale,
            force=force,
            log_path=log_path,
        )
    except WorkspaceMismatchError:
        return _error_redirect(
            request,
            ticket_id,
            "Workspace does not match the reviewed diff. Use force to override.",
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


async def action_revise(request: Request) -> RedirectResponse | HTMLResponse:
    """POST /tickets/{ticket_id}/revise — send a ticket back for revision."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    form = await request.form()
    finding_text = form.get("finding", "").strip()
    add_findings = [finding_text] if finding_text else None
    reset_cycles = form.get("reset_cycles") == "on"

    from capsaicin.app.commands.revise_ticket import revise

    try:
        revise(
            conn=conn,
            project_id=project_id,
            ticket_id=ticket_id,
            add_findings=add_findings,
            reset_cycles=reset_cycles,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


async def action_defer(request: Request) -> RedirectResponse | HTMLResponse:
    """POST /tickets/{ticket_id}/defer — defer or abandon a ticket."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    form = await request.form()
    rationale = form.get("rationale", "").strip() or None
    abandon = form.get("abandon") == "on"

    from capsaicin.app.commands.defer_ticket import defer

    try:
        defer(
            conn=conn,
            project_id=project_id,
            ticket_id=ticket_id,
            rationale=rationale,
            abandon=abandon,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


async def action_unblock(request: Request) -> RedirectResponse | HTMLResponse:
    """POST /tickets/{ticket_id}/unblock — return a blocked ticket to ready."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    form = await request.form()
    reset_cycles = form.get("reset_cycles") == "on"

    from capsaicin.app.commands.unblock_ticket import unblock

    try:
        unblock(
            conn=conn,
            project_id=project_id,
            ticket_id=ticket_id,
            reset_cycles=reset_cycles,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


# ---------------------------------------------------------------------------
# Workflow trigger actions (run, review, resume, loop)
# ---------------------------------------------------------------------------


async def action_run(request: Request) -> RedirectResponse:
    """POST /tickets/{ticket_id}/run — trigger an implementation run."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    from capsaicin.app.commands.run_ticket import run

    try:
        run(
            conn=conn,
            project_id=project_id,
            config=config,
            ticket_id=ticket_id,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


async def action_review(request: Request) -> RedirectResponse:
    """POST /tickets/{ticket_id}/review — trigger a review run."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    form = await request.form()
    allow_drift = form.get("allow_drift") == "on"

    from capsaicin.app.commands.review_ticket import review

    try:
        review(
            conn=conn,
            project_id=project_id,
            config=config,
            ticket_id=ticket_id,
            allow_drift=allow_drift,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


async def action_loop(request: Request) -> RedirectResponse:
    """POST /tickets/{ticket_id}/loop — trigger the implement-review loop."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    from capsaicin.app.commands.loop import loop

    try:
        loop(
            conn=conn,
            project_id=project_id,
            config=config,
            ticket_id=ticket_id,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


async def action_resume(request: Request) -> RedirectResponse:
    """POST /actions/resume — resume from the current orchestrator state."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path

    from capsaicin.app.commands.resume import resume

    try:
        result = resume(
            conn=conn,
            project_id=project_id,
            config=config,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        from urllib.parse import quote

        return RedirectResponse(f"/?error={quote(str(exc))}", status_code=303)

    if result.ticket_id:
        return RedirectResponse(f"/tickets/{result.ticket_id}", status_code=303)
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_redirect(request: Request, ticket_id: str, message: str) -> RedirectResponse:
    """Redirect back to the ticket with an error query parameter."""
    from urllib.parse import quote

    return RedirectResponse(
        f"/tickets/{ticket_id}?error={quote(message)}", status_code=303
    )
