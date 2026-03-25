"""Starlette ASGI application for the local operator UI.

Connection model
~~~~~~~~~~~~~~~~
Each request gets its own short-lived SQLite connection via the
``db_connection`` middleware.  Connections are opened at request start
and closed at request end so that no long-lived transactions block
concurrent CLI usage.  WAL mode is **not** enabled by default — the
single-operator local model does not require it, and enabling it should
be a deliberate decision documented in the web-layer work.

The project context (project_id, config, log_path, db_path) is resolved
once at startup and stored on ``app.state``.  Per-request handlers read
from ``app.state`` and obtain a fresh connection from ``request.state``.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from capsaicin.web.middleware import DBConnectionMiddleware
from capsaicin.web.routes.actions import (
    action_add_dependency,
    action_approve,
    action_complete,
    action_create_ticket,
    action_defer,
    action_delete_ticket_override,
    action_loop,
    action_resume,
    action_review,
    action_revise,
    action_run,
    action_set_ticket_override,
    action_shutdown,
    action_unblock,
)
from capsaicin.web.routes.dashboard import dashboard
from capsaicin.web.routes.doctor import doctor_page
from capsaicin.web.routes.events import (
    dashboard_events,
    epic_events,
    planning_events,
    ticket_events,
)
from capsaicin.web.routes.partials import (
    partial_activity,
    partial_blocked,
    partial_inbox,
    partial_next_runnable,
    partial_orchestrator,
    partial_queue,
    partial_ticket_content,
)
from capsaicin.web.routes.planning import epic_detail, planning_dashboard
from capsaicin.web.routes.planning_actions import (
    action_approve_epic,
    action_continue_implementation,
    action_create_epic,
    action_defer_epic,
    action_delete_epic_override,
    action_draft_epic,
    action_materialize_epic,
    action_plan_loop,
    action_review_epic,
    action_revise_epic,
    action_set_epic_override,
    action_unblock_epic,
)
from capsaicin.web.routes.evidence_actions import (
    action_create_evidence,
    action_create_requirement,
    action_delete_evidence,
    action_paste_output,
    action_satisfy_requirement,
    action_waive_requirement,
)
from capsaicin.web.routes.planning_partials import (
    partial_epic_content,
    partial_planning_active,
    partial_planning_approved,
    partial_planning_blocked,
    partial_planning_gate,
    partial_planning_queue,
)
from capsaicin.web.routes.tickets import ticket_detail

_PACKAGE_DIR = Path(__file__).parent
_STATIC_DIR = _PACKAGE_DIR / "static"


def create_app(
    db_path: str | Path,
    project_id: str,
    config_path: str | Path,
    log_path: str | Path,
) -> Starlette:
    """Build and return the Starlette ASGI application.

    Parameters are resolved once by the CLI launcher and baked into
    ``app.state`` so route handlers never repeat project discovery.
    """
    routes = [
        Route("/", dashboard, name="dashboard"),
        Route("/doctor", doctor_page, name="doctor"),
        Route(
            "/tickets/new",
            action_create_ticket,
            methods=["POST"],
            name="action_create_ticket",
        ),
        Route("/tickets/{ticket_id}", ticket_detail, name="ticket_detail"),
        Route(
            "/tickets/{ticket_id}/dep",
            action_add_dependency,
            methods=["POST"],
            name="action_add_dependency",
        ),
        # Ticket action routes — POST only
        Route(
            "/tickets/{ticket_id}/approve",
            action_approve,
            methods=["POST"],
            name="action_approve",
        ),
        Route(
            "/tickets/{ticket_id}/revise",
            action_revise,
            methods=["POST"],
            name="action_revise",
        ),
        Route(
            "/tickets/{ticket_id}/defer",
            action_defer,
            methods=["POST"],
            name="action_defer",
        ),
        Route(
            "/tickets/{ticket_id}/complete",
            action_complete,
            methods=["POST"],
            name="action_complete",
        ),
        Route(
            "/tickets/{ticket_id}/unblock",
            action_unblock,
            methods=["POST"],
            name="action_unblock",
        ),
        Route(
            "/tickets/{ticket_id}/run",
            action_run,
            methods=["POST"],
            name="action_run",
        ),
        Route(
            "/tickets/{ticket_id}/review",
            action_review,
            methods=["POST"],
            name="action_review",
        ),
        Route(
            "/tickets/{ticket_id}/loop",
            action_loop,
            methods=["POST"],
            name="action_loop",
        ),
        # Ticket override routes
        Route(
            "/tickets/{ticket_id}/overrides",
            action_set_ticket_override,
            methods=["POST"],
            name="action_set_ticket_override",
        ),
        Route(
            "/tickets/{ticket_id}/overrides/{override_id}/delete",
            action_delete_ticket_override,
            methods=["POST"],
            name="action_delete_ticket_override",
        ),
        Route(
            "/actions/resume",
            action_resume,
            methods=["POST"],
            name="action_resume",
        ),
        Route(
            "/actions/shutdown",
            action_shutdown,
            methods=["POST"],
            name="action_shutdown",
        ),
        # Ticket partials
        Route("/partials/inbox", partial_inbox, name="partial_inbox"),
        Route("/partials/queue", partial_queue, name="partial_queue"),
        Route("/partials/activity", partial_activity, name="partial_activity"),
        Route("/partials/blocked", partial_blocked, name="partial_blocked"),
        Route(
            "/partials/next-runnable",
            partial_next_runnable,
            name="partial_next_runnable",
        ),
        Route(
            "/partials/orchestrator", partial_orchestrator, name="partial_orchestrator"
        ),
        Route(
            "/partials/tickets/{ticket_id}",
            partial_ticket_content,
            name="partial_ticket_content",
        ),
        # Planning views
        Route("/planning", planning_dashboard, name="planning_dashboard"),
        Route(
            "/epics/new",
            action_create_epic,
            methods=["POST"],
            name="action_create_epic",
        ),
        Route("/epics/{epic_id}", epic_detail, name="epic_detail"),
        # Planning action routes — POST only
        Route(
            "/epics/{epic_id}/approve",
            action_approve_epic,
            methods=["POST"],
            name="action_approve_epic",
        ),
        Route(
            "/epics/{epic_id}/revise",
            action_revise_epic,
            methods=["POST"],
            name="action_revise_epic",
        ),
        Route(
            "/epics/{epic_id}/defer",
            action_defer_epic,
            methods=["POST"],
            name="action_defer_epic",
        ),
        Route(
            "/epics/{epic_id}/unblock",
            action_unblock_epic,
            methods=["POST"],
            name="action_unblock_epic",
        ),
        Route(
            "/epics/{epic_id}/draft",
            action_draft_epic,
            methods=["POST"],
            name="action_draft_epic",
        ),
        Route(
            "/epics/{epic_id}/review",
            action_review_epic,
            methods=["POST"],
            name="action_review_epic",
        ),
        Route(
            "/epics/{epic_id}/loop",
            action_plan_loop,
            methods=["POST"],
            name="action_plan_loop",
        ),
        Route(
            "/epics/{epic_id}/materialize",
            action_materialize_epic,
            methods=["POST"],
            name="action_materialize_epic",
        ),
        Route(
            "/epics/{epic_id}/continue-implementation",
            action_continue_implementation,
            methods=["POST"],
            name="action_continue_implementation",
        ),
        # Epic override routes
        Route(
            "/epics/{epic_id}/overrides",
            action_set_epic_override,
            methods=["POST"],
            name="action_set_epic_override",
        ),
        Route(
            "/epics/{epic_id}/overrides/{override_id}/delete",
            action_delete_epic_override,
            methods=["POST"],
            name="action_delete_epic_override",
        ),
        # Evidence action routes — POST only
        Route(
            "/epics/{epic_id}/evidence",
            action_create_evidence,
            methods=["POST"],
            name="action_create_evidence",
        ),
        Route(
            "/epics/{epic_id}/requirements",
            action_create_requirement,
            methods=["POST"],
            name="action_create_requirement",
        ),
        Route(
            "/epics/{epic_id}/requirements/{req_id}/satisfy",
            action_satisfy_requirement,
            methods=["POST"],
            name="action_satisfy_requirement",
        ),
        Route(
            "/epics/{epic_id}/requirements/{req_id}/waive",
            action_waive_requirement,
            methods=["POST"],
            name="action_waive_requirement",
        ),
        Route(
            "/epics/{epic_id}/requirements/{req_id}/paste-output",
            action_paste_output,
            methods=["POST"],
            name="action_paste_output",
        ),
        Route(
            "/epics/{epic_id}/evidence/{evidence_id}/delete",
            action_delete_evidence,
            methods=["POST"],
            name="action_delete_evidence",
        ),
        # Planning partials
        Route(
            "/partials/planning/gate",
            partial_planning_gate,
            name="partial_planning_gate",
        ),
        Route(
            "/partials/planning/active",
            partial_planning_active,
            name="partial_planning_active",
        ),
        Route(
            "/partials/planning/approved",
            partial_planning_approved,
            name="partial_planning_approved",
        ),
        Route(
            "/partials/planning/blocked",
            partial_planning_blocked,
            name="partial_planning_blocked",
        ),
        Route(
            "/partials/planning/queue",
            partial_planning_queue,
            name="partial_planning_queue",
        ),
        Route(
            "/partials/epics/{epic_id}",
            partial_epic_content,
            name="partial_epic_content",
        ),
        # SSE events
        Route("/events/dashboard", dashboard_events, name="dashboard_events"),
        Route("/events/tickets/{ticket_id}", ticket_events, name="ticket_events"),
        Route("/events/planning", planning_events, name="planning_events"),
        Route("/events/epics/{epic_id}", epic_events, name="epic_events"),
        Mount(
            "/static",
            app=StaticFiles(directory=str(_STATIC_DIR)),
            name="static",
        ),
    ]

    middleware = [
        Middleware(DBConnectionMiddleware, db_path=str(db_path)),
    ]

    app = Starlette(routes=routes, middleware=middleware)

    # Bake resolved project context onto app.state for handlers
    app.state.db_path = str(db_path)
    app.state.project_id = project_id
    app.state.config_path = str(config_path)
    app.state.log_path = str(log_path)

    return app
