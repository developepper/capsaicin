"""POST action routes for planning human-gate decisions and workflow triggers.

Each handler delegates to the shared planning command services from
``app.commands`` and redirects back to the epic detail or planning
dashboard.  This keeps action semantics identical to the CLI — the web
UI does not reinterpret state-machine rules.

Long-running commands (draft, review, plan-loop) are dispatched to a
background thread so the HTTP response returns immediately.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from starlette.requests import Request
from starlette.responses import RedirectResponse

from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.errors import CapsaicinError
from capsaicin.state_machine import IllegalPlanningTransitionError

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Epic creation
# ---------------------------------------------------------------------------


async def action_create_epic(request: Request) -> RedirectResponse:
    """POST /epics/new — create a new planned epic from a problem statement."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path

    form = await request.form()
    problem_statement = form.get("problem_statement", "").strip()

    if not problem_statement:
        url = request.url_for("planning_dashboard").include_query_params(
            error="Problem statement is required"
        )
        return RedirectResponse(str(url), status_code=303)

    from capsaicin.app.commands.new_epic import new_epic

    try:
        result = new_epic(
            conn=conn,
            project_id=project_id,
            problem_statement=problem_statement,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        url = request.url_for("planning_dashboard").include_query_params(error=str(exc))
        return RedirectResponse(str(url), status_code=303)

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=result.epic_id)),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Human-gate actions (sync)
# ---------------------------------------------------------------------------


async def action_approve_epic(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/approve — approve an epic at the human gate."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]

    form = await request.form()
    rationale = form.get("rationale", "").strip() or None
    force = form.get("force") == "on"

    from capsaicin.app.commands.approve_epic import approve

    # Determine repo_root for materialization
    repo_root: Path | None = None
    config_path = request.app.state.config_path
    if config_path:
        repo_root = Path(config_path).parent

    try:
        approve(
            conn=conn,
            project_id=project_id,
            epic_id=epic_id,
            rationale=rationale,
            log_path=log_path,
            repo_root=repo_root,
            force=force,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, epic_id, str(exc))

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_revise_epic(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/revise — send an epic back for revision."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]

    form = await request.form()
    finding_text = form.get("finding", "").strip()
    add_findings = [finding_text] if finding_text else None

    from capsaicin.app.commands.revise_epic import revise

    try:
        revise(
            conn=conn,
            project_id=project_id,
            epic_id=epic_id,
            add_findings=add_findings,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, epic_id, str(exc))

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_defer_epic(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/defer — defer (block) an epic."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]

    form = await request.form()
    rationale = form.get("rationale", "").strip() or None

    from capsaicin.app.commands.defer_epic import defer

    try:
        defer(
            conn=conn,
            project_id=project_id,
            epic_id=epic_id,
            rationale=rationale,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, epic_id, str(exc))

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_unblock_epic(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/unblock — return a blocked epic to new."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    epic_id = request.path_params["epic_id"]
    log_path = request.app.state.log_path

    from capsaicin.app.commands.unblock_epic import unblock

    try:
        unblock(
            conn=conn,
            project_id=project_id,
            epic_id=epic_id,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError, IllegalPlanningTransitionError) as exc:
        return _error_redirect(request, epic_id, str(exc))

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


# ---------------------------------------------------------------------------
# Workflow trigger actions (async, background)
# ---------------------------------------------------------------------------


async def action_draft_epic(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/draft — trigger a draft run (background)."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]

    _run_in_background(_bg_draft, db_path, project_id, epic_id, log_path)

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_review_epic(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/review — trigger a review run (background)."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]

    _run_in_background(_bg_review_epic, db_path, project_id, epic_id, log_path)

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_plan_loop(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/loop — trigger the planning loop (background)."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    config_path = request.app.state.config_path
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]

    _run_in_background(
        _bg_plan_loop, db_path, project_id, config_path, epic_id, log_path
    )

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_continue_implementation(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/continue-implementation — trigger implementation loop for next ready ticket."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    config_path = request.app.state.config_path
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]
    conn = request.state.conn

    form = await request.form()
    ticket_id = form.get("ticket_id", "").strip() or None

    # Find the next ready ticket scoped to this epic
    from capsaicin.app.queries.planning_detail import _load_impl_tickets

    impl_tickets = _load_impl_tickets(conn, epic_id)

    if ticket_id:
        # Validate the selected ticket belongs to this epic and is eligible
        match = [t for t in impl_tickets if t["id"] == ticket_id]
        if not match:
            return _error_redirect(
                request, epic_id, "Selected ticket does not belong to this epic."
            )
        t = match[0]
        if t["status"] not in ("ready", "revise"):
            return _error_redirect(
                request,
                epic_id,
                f"Ticket is in '{t['status']}' status, not ready for implementation.",
            )
        if t["status"] == "ready" and not t["is_ready"]:
            return _error_redirect(
                request, epic_id, "Ticket has unsatisfied dependencies."
            )
    else:
        # Auto-select: prefer revise, then ready with deps satisfied
        candidate = None
        for t in impl_tickets:
            if t["status"] == "revise":
                candidate = t
                break
        if candidate is None:
            for t in impl_tickets:
                if t["status"] == "ready" and t["is_ready"]:
                    candidate = t
                    break
        if candidate is None:
            return _error_redirect(
                request,
                epic_id,
                "No tickets are ready for implementation (all blocked by dependencies or already in progress/done).",
            )
        ticket_id = candidate["id"]

    _run_in_background(
        _bg_continue_implementation,
        db_path,
        project_id,
        config_path,
        ticket_id,
        log_path,
    )

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_materialize_epic(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/materialize — re-materialize an approved epic."""
    conn = request.state.conn
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]
    config_path = request.app.state.config_path

    form = await request.form()
    force = form.get("force") == "on"

    repo_root = Path(config_path).parent if config_path else None
    if repo_root is None:
        return _error_redirect(
            request, epic_id, "Cannot determine repo root for materialization."
        )

    from capsaicin.app.commands.materialize_epic import materialize

    try:
        materialize(
            conn=conn,
            project_id=project_id,
            epic_id=epic_id,
            repo_root=repo_root,
            force=force,
            log_path=log_path,
        )
    except (ValueError, CapsaicinError) as exc:
        return _error_redirect(request, epic_id, str(exc))

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_redirect(request: Request, epic_id: str, message: str) -> RedirectResponse:
    """Redirect back to the epic with an error query parameter."""
    url = request.url_for("epic_detail", epic_id=epic_id).include_query_params(
        error=message
    )
    return RedirectResponse(str(url), status_code=303)


def _run_in_background(fn, *args) -> None:
    """Fire a long-running command in a daemon thread."""
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()


def _bg_draft(db_path, project_id, epic_id, log_path) -> None:
    from capsaicin.app.commands.draft_epic import draft

    conn = get_connection(db_path)
    try:
        draft(conn=conn, project_id=project_id, epic_id=epic_id, log_path=log_path)
    except Exception:
        _log.exception("Background draft failed for epic %s", epic_id)
    finally:
        conn.close()


def _bg_review_epic(db_path, project_id, epic_id, log_path) -> None:
    from capsaicin.app.commands.review_epic import review

    conn = get_connection(db_path)
    try:
        review(conn=conn, project_id=project_id, epic_id=epic_id, log_path=log_path)
    except Exception:
        _log.exception("Background review failed for epic %s", epic_id)
    finally:
        conn.close()


def _bg_plan_loop(db_path, project_id, config_path, epic_id, log_path) -> None:
    from capsaicin.app.commands.plan_loop import plan_loop

    config = load_config(config_path)
    conn = get_connection(db_path)
    try:
        plan_loop(
            conn=conn,
            project_id=project_id,
            config=config,
            epic_id=epic_id,
            log_path=log_path,
        )
    except Exception:
        _log.exception("Background plan loop failed for epic %s", epic_id)
    finally:
        conn.close()


def _bg_continue_implementation(
    db_path, project_id, config_path, ticket_id, log_path
) -> None:
    from capsaicin.app.commands.loop import loop

    config = load_config(config_path)
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
        _log.exception("Background implementation loop failed for ticket %s", ticket_id)
    finally:
        conn.close()
