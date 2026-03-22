"""SSE endpoints for narrow live updates (T05).

Each endpoint opens its own short-lived SQLite connection per poll
cycle rather than holding one connection for the lifetime of the
stream.  This keeps connection usage compatible with concurrent CLI
access and avoids holding SQLite locks across idle sleep intervals.

Event payloads are small JSON identifiers — the client uses them to
trigger partial re-renders via existing HTMX partial endpoints rather
than receiving full HTML over the stream.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import AsyncGenerator

from starlette.requests import Request
from starlette.responses import StreamingResponse

from capsaicin.db import get_connection

# Poll interval in seconds.  3 s is responsive enough for a local
# operator UI without being wasteful on a single-user SQLite database.
_POLL_INTERVAL = 3

# Keepalive comment interval (heartbeat to prevent proxy/browser
# timeouts).  Sent as an SSE comment line (": keepalive\n\n").
_KEEPALIVE_INTERVAL = 15


def _sse_event(event: str, data: str = "") -> str:
    """Format a single SSE event."""
    lines = [f"event: {event}"]
    for line in (data or "").split("\n"):
        lines.append(f"data: {line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _sse_comment(text: str = "keepalive") -> str:
    return f": {text}\n\n"


# ---------------------------------------------------------------------------
# Dashboard snapshot — detect changes by comparing lightweight checksums
# ---------------------------------------------------------------------------


def _dashboard_snapshot(conn: sqlite3.Connection, project_id: str) -> dict:
    """Return a lightweight state snapshot for change detection."""
    # Orchestrator state
    orch = conn.execute(
        "SELECT status, active_ticket_id, active_run_id, updated_at "
        "FROM orchestrator_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    orch_key = (
        (
            orch["status"],
            orch["active_ticket_id"],
            orch["active_run_id"],
            orch["updated_at"],
        )
        if orch
        else None
    )

    # Inbox: human-gate ticket count and latest status_changed_at
    inbox_row = conn.execute(
        "SELECT COUNT(*) AS cnt, MAX(status_changed_at) AS latest "
        "FROM tickets WHERE project_id = ? AND status = 'human-gate'",
        (project_id,),
    ).fetchone()
    inbox_key = (inbox_row["cnt"], inbox_row["latest"]) if inbox_row else (0, None)

    # Queue: total + max updated_at for coarse change detection
    queue_row = conn.execute(
        "SELECT COUNT(*) AS cnt, MAX(status_changed_at) AS latest "
        "FROM tickets WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    queue_key = (queue_row["cnt"], queue_row["latest"]) if queue_row else (0, None)

    # Blocked: blocked ticket count and latest status_changed_at
    blocked_row = conn.execute(
        "SELECT COUNT(*) AS cnt, MAX(status_changed_at) AS latest "
        "FROM tickets WHERE project_id = ? AND status = 'blocked'",
        (project_id,),
    ).fetchone()
    blocked_key = (
        (blocked_row["cnt"], blocked_row["latest"]) if blocked_row else (0, None)
    )

    # Next runnable: first ready ticket with deps satisfied.  Track its id
    # and the latest status_changed_at across ready tickets so we detect
    # when the next-runnable changes due to completions or new tickets.
    ready_row = conn.execute(
        "SELECT COUNT(*) AS cnt, MAX(status_changed_at) AS latest "
        "FROM tickets WHERE project_id = ? AND status = 'ready'",
        (project_id,),
    ).fetchone()
    # Also factor in done-count changes (dependency satisfaction).
    done_row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM tickets WHERE project_id = ? AND status = 'done'",
        (project_id,),
    ).fetchone()
    next_runnable_key = (
        ready_row["cnt"] if ready_row else 0,
        ready_row["latest"] if ready_row else None,
        done_row["cnt"] if done_row else 0,
    )

    # Activity: latest agent_run started_at or finished_at
    act_row = conn.execute(
        "SELECT MAX(COALESCE(finished_at, started_at)) AS latest "
        "FROM agent_runs ar JOIN tickets t ON t.id = ar.ticket_id "
        "WHERE t.project_id = ?",
        (project_id,),
    ).fetchone()
    activity_key = act_row["latest"] if act_row else None

    return {
        "orchestrator": orch_key,
        "inbox": inbox_key,
        "queue": queue_key,
        "blocked": blocked_key,
        "next_runnable": next_runnable_key,
        "activity": activity_key,
    }


async def dashboard_events(request: Request) -> StreamingResponse:
    """Stream dashboard change events via SSE.

    Polls the database every few seconds and emits named events when
    relevant data has changed.  The client uses these events to trigger
    partial re-renders through the existing HTMX partial endpoints.
    """
    db_path = request.app.state.db_path
    project_id = request.app.state.project_id

    async def event_generator() -> AsyncGenerator[str, None]:
        prev: dict = {}
        ticks_since_keepalive = 0

        try:
            while True:
                if await request.is_disconnected():
                    break

                conn = get_connection(db_path)
                try:
                    snap = _dashboard_snapshot(conn, project_id)
                finally:
                    conn.close()

                # Emit events for each changed section
                changed = False
                for key in (
                    "orchestrator",
                    "inbox",
                    "queue",
                    "blocked",
                    "next_runnable",
                    "activity",
                ):
                    if snap[key] != prev.get(key):
                        yield _sse_event(key, json.dumps({"ts": str(snap[key])}))
                        changed = True

                prev = snap

                if changed:
                    ticks_since_keepalive = 0
                else:
                    ticks_since_keepalive += _POLL_INTERVAL
                    if ticks_since_keepalive >= _KEEPALIVE_INTERVAL:
                        yield _sse_comment()
                        ticks_since_keepalive = 0

                await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Ticket detail snapshot
# ---------------------------------------------------------------------------


def _ticket_snapshot(conn: sqlite3.Connection, ticket_id: str) -> dict | None:
    """Return a lightweight state snapshot for a single ticket."""
    row = conn.execute(
        "SELECT status, status_changed_at, current_cycle, "
        "current_impl_attempt, current_review_attempt, "
        "gate_reason, blocked_reason, updated_at "
        "FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None

    ticket_key = tuple(row)

    # Findings: count + latest updated_at
    findings_row = conn.execute(
        "SELECT COUNT(*) AS cnt, MAX(updated_at) AS latest "
        "FROM findings WHERE ticket_id = ? AND disposition = 'open'",
        (ticket_id,),
    ).fetchone()
    findings_key = (
        (findings_row["cnt"], findings_row["latest"]) if findings_row else (0, None)
    )

    # Criteria: latest updated_at
    crit_row = conn.execute(
        "SELECT MAX(updated_at) AS latest FROM acceptance_criteria WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchone()
    criteria_key = crit_row["latest"] if crit_row else None

    # Latest run
    run_row = conn.execute(
        "SELECT MAX(COALESCE(finished_at, started_at)) AS latest "
        "FROM agent_runs WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchone()
    run_key = run_row["latest"] if run_row else None

    return {
        "ticket": ticket_key,
        "findings": findings_key,
        "criteria": criteria_key,
        "runs": run_key,
    }


async def ticket_events(request: Request) -> StreamingResponse:
    """Stream change events for a single ticket via SSE.

    Emits a ``ticket-updated`` event whenever any aspect of the ticket
    (status, findings, criteria, runs) changes.
    """
    db_path = request.app.state.db_path
    ticket_id = request.path_params["ticket_id"]

    async def event_generator() -> AsyncGenerator[str, None]:
        prev: dict | None = None
        ticks_since_keepalive = 0

        try:
            while True:
                if await request.is_disconnected():
                    break

                conn = get_connection(db_path)
                try:
                    snap = _ticket_snapshot(conn, ticket_id)
                finally:
                    conn.close()

                if snap is None:
                    # Ticket deleted or never existed — close the stream
                    yield _sse_event("ticket-gone", json.dumps({"ticket_id": ticket_id}))
                    break

                if snap != prev:
                    yield _sse_event(
                        "ticket-updated",
                        json.dumps({"ticket_id": ticket_id, "status": snap["ticket"][0]}),
                    )
                    prev = snap
                    ticks_since_keepalive = 0
                else:
                    ticks_since_keepalive += _POLL_INTERVAL
                    if ticks_since_keepalive >= _KEEPALIVE_INTERVAL:
                        yield _sse_comment()
                        ticks_since_keepalive = 0

                await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
