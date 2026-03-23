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
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse

from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.errors import CapsaicinError
from capsaicin.state_machine import IllegalPlanningTransitionError

_log = logging.getLogger(__name__)


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
        return _error_redirect(epic_id, str(exc))

    return RedirectResponse(f"/epics/{epic_id}", status_code=303)


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
        return _error_redirect(epic_id, str(exc))

    return RedirectResponse(f"/epics/{epic_id}", status_code=303)


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
        return _error_redirect(epic_id, str(exc))

    return RedirectResponse(f"/epics/{epic_id}", status_code=303)


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
        return _error_redirect(epic_id, str(exc))

    return RedirectResponse(f"/epics/{epic_id}", status_code=303)


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

    return RedirectResponse(f"/epics/{epic_id}", status_code=303)


async def action_review_epic(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/review — trigger a review run (background)."""
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id
    log_path = request.app.state.log_path
    epic_id = request.path_params["epic_id"]

    _run_in_background(_bg_review_epic, db_path, project_id, epic_id, log_path)

    return RedirectResponse(f"/epics/{epic_id}", status_code=303)


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

    return RedirectResponse(f"/epics/{epic_id}", status_code=303)


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
            epic_id, "Cannot determine repo root for materialization."
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
        return _error_redirect(epic_id, str(exc))

    return RedirectResponse(f"/epics/{epic_id}", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_redirect(epic_id: str, message: str) -> RedirectResponse:
    """Redirect back to the epic with an error query parameter."""
    return RedirectResponse(f"/epics/{epic_id}?error={quote(message)}", status_code=303)


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
