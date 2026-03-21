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
from capsaicin.web.routes.dashboard import dashboard
from capsaicin.web.routes.events import dashboard_events, ticket_events
from capsaicin.web.routes.partials import (
    partial_activity,
    partial_blocked,
    partial_inbox,
    partial_next_runnable,
    partial_orchestrator,
    partial_queue,
    partial_ticket_content,
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
        Route("/tickets/{ticket_id}", ticket_detail, name="ticket_detail"),
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
        Route("/events/dashboard", dashboard_events, name="dashboard_events"),
        Route("/events/tickets/{ticket_id}", ticket_events, name="ticket_events"),
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
