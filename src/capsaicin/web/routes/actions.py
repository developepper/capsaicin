"""POST action routes for human-gate decisions and workflow triggers.

Each handler delegates to the shared command services from ``app.commands``
and redirects back to the ticket detail or dashboard view.  This keeps
action semantics identical to the CLI — the web UI does not reinterpret
state-machine rules.

Long-running commands (run, review, loop, resume) are dispatched to a
background thread so the HTTP response returns immediately.  Each
background task opens its own short-lived DB connection — the request
connection is already closed by the time the task executes.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from starlette.requests import Request
from starlette.responses import RedirectResponse, HTMLResponse

from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.errors import CapsaicinError
from capsaicin.web.templating import templates

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ticket creation
# ---------------------------------------------------------------------------


async def action_create_ticket(request: Request) -> RedirectResponse:
    """POST /tickets/new — create a new ticket from the dashboard form."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path

    form = await request.form()
    title = form.get("title", "").strip()
    description = form.get("description", "").strip()
    criteria_raw = form.get("criteria", "").strip()

    if not title or not description:
        error = "Title and description are required."
        url = request.url_for("dashboard").include_query_params(error=error)
        return RedirectResponse(str(url), status_code=303)

    criteria = (
        [c.strip() for c in criteria_raw.splitlines() if c.strip()]
        if criteria_raw
        else []
    )

    from capsaicin.ticket_add import add_ticket_inline

    try:
        ticket_id = add_ticket_inline(
            conn=conn,
            project_id=project_id,
            title=title,
            description=description,
            criteria=criteria,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        url = request.url_for("dashboard").include_query_params(error=str(exc))
        return RedirectResponse(str(url), status_code=303)

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


# ---------------------------------------------------------------------------
# Ticket dependencies
# ---------------------------------------------------------------------------


async def action_add_dependency(request: Request) -> RedirectResponse:
    """POST /tickets/{ticket_id}/dep — add a dependency on another ticket."""
    conn = request.state.conn
    ticket_id = request.path_params["ticket_id"]

    form = await request.form()
    depends_on_id = form.get("depends_on_id", "").strip()

    if not depends_on_id:
        return _error_redirect(request, ticket_id, "Dependency ticket ID is required.")

    from capsaicin.ticket_dep import add_dependency

    try:
        add_dependency(conn, ticket_id, depends_on_id)
    except ValueError as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


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

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


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

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


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

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


async def action_complete(request: Request) -> RedirectResponse | HTMLResponse:
    """POST /tickets/{ticket_id}/complete — mark a pr-ready ticket as done."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    form = await request.form()
    rationale = form.get("rationale", "").strip() or None

    from capsaicin.app.commands.complete_ticket import complete

    try:
        complete(
            conn=conn,
            project_id=project_id,
            ticket_id=ticket_id,
            rationale=rationale,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, ticket_id, str(exc))

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


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

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


# ---------------------------------------------------------------------------
# Workflow trigger actions (run, review, resume, loop)
# ---------------------------------------------------------------------------


async def action_run(request: Request) -> RedirectResponse:
    """POST /tickets/{ticket_id}/run — trigger an implementation run."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    _run_in_background(_bg_run, db_path, project_id, config, ticket_id, log_path)

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


async def action_review(request: Request) -> RedirectResponse:
    """POST /tickets/{ticket_id}/review — trigger a review run."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    form = await request.form()
    allow_drift = form.get("allow_drift") == "on"

    _run_in_background(
        _bg_review, db_path, project_id, config, ticket_id, allow_drift, log_path
    )

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


async def action_loop(request: Request) -> RedirectResponse:
    """POST /tickets/{ticket_id}/loop — trigger the implement-review loop."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path
    ticket_id = request.path_params["ticket_id"]

    _run_in_background(_bg_loop, db_path, project_id, config, ticket_id, log_path)

    return RedirectResponse(
        str(request.url_for("ticket_detail", ticket_id=ticket_id)), status_code=303
    )


async def action_resume(request: Request) -> RedirectResponse:
    """POST /actions/resume — resume from the current orchestrator state."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    config = load_config(request.app.state.config_path)
    log_path = request.app.state.log_path

    _run_in_background(_bg_resume, db_path, project_id, config, log_path)

    return RedirectResponse(str(request.url_for("dashboard")), status_code=303)


# ---------------------------------------------------------------------------
# Server shutdown
# ---------------------------------------------------------------------------


async def action_shutdown(request: Request) -> HTMLResponse:
    """POST /actions/shutdown — stop the server from the browser."""
    from capsaicin.web.server import shutdown_server

    # Schedule the shutdown slightly after the response is sent so the
    # browser gets a confirmation page before the process exits.
    import threading

    threading.Timer(0.5, shutdown_server).start()

    return HTMLResponse(
        "<html><body style='font-family:system-ui;text-align:center;padding:4rem'>"
        "<h1>Server stopped</h1>"
        "<p>The capsaicin UI server has been shut down.</p>"
        "<p style='color:#6c757d'>You can close this tab.</p>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_redirect(request: Request, ticket_id: str, message: str) -> RedirectResponse:
    """Redirect back to the ticket with an error query parameter."""
    url = request.url_for("ticket_detail", ticket_id=ticket_id).include_query_params(
        error=message
    )
    return RedirectResponse(str(url), status_code=303)


# ---------------------------------------------------------------------------
# Background execution helpers
# ---------------------------------------------------------------------------


def _run_in_background(fn, *args) -> None:
    """Fire a long-running command in a daemon thread.

    Each background function opens its own DB connection so it is
    independent of the request lifecycle.
    """
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()


def _bg_run(db_path, project_id, config, ticket_id, log_path) -> None:
    from capsaicin.app.commands.run_ticket import run

    conn = get_connection(db_path)
    try:
        run(
            conn=conn,
            project_id=project_id,
            config=config,
            ticket_id=ticket_id,
            log_path=log_path,
        )
    except Exception:
        _log.exception("Background run failed for ticket %s", ticket_id)
    finally:
        conn.close()


def _bg_review(db_path, project_id, config, ticket_id, allow_drift, log_path) -> None:
    from capsaicin.app.commands.review_ticket import review

    conn = get_connection(db_path)
    try:
        review(
            conn=conn,
            project_id=project_id,
            config=config,
            ticket_id=ticket_id,
            allow_drift=allow_drift,
            log_path=log_path,
        )
    except Exception:
        _log.exception("Background review failed for ticket %s", ticket_id)
    finally:
        conn.close()


def _bg_loop(db_path, project_id, config, ticket_id, log_path) -> None:
    from capsaicin.app.commands.loop import loop

    conn = get_connection(db_path)
    try:
        loop(
            conn=conn,
            project_id=project_id,
            config=config,
            ticket_id=ticket_id,
            log_path=log_path,
        )
    except Exception:
        _log.exception("Background loop failed for ticket %s", ticket_id)
    finally:
        conn.close()


def _bg_resume(db_path, project_id, config, log_path) -> None:
    from capsaicin.app.commands.resume import resume

    conn = get_connection(db_path)
    try:
        resume(
            conn=conn,
            project_id=project_id,
            config=config,
            log_path=log_path,
        )
    except Exception:
        _log.exception("Background resume failed")
    finally:
        conn.close()
