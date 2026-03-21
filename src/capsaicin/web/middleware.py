"""Request-scoped middleware for the web UI."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from capsaicin.db import get_connection


class DBConnectionMiddleware(BaseHTTPMiddleware):
    """Open a per-request SQLite connection and close it when done.

    The connection is stored on ``request.state.conn`` so route handlers
    can use it directly.  Keeping connections short-lived avoids holding
    SQLite locks across slow template rendering or network I/O and stays
    compatible with concurrent CLI usage on the same database.
    """

    def __init__(self, app, db_path: str) -> None:
        super().__init__(app)
        self.db_path = db_path

    async def dispatch(self, request: Request, call_next) -> Response:
        conn = get_connection(self.db_path)
        request.state.conn = conn
        try:
            response = await call_next(request)
            return response
        finally:
            conn.close()
