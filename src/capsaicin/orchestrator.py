"""Orchestrator state management module (T09).

Tracks active ticket/run and manages cycle/retry counters on tickets.
"""

from __future__ import annotations

import sqlite3

from capsaicin.queries import now_utc


def _assert_updated(cursor: sqlite3.Cursor, entity: str, id_value: str) -> None:
    """Raise ValueError if an UPDATE affected zero rows."""
    if cursor.rowcount == 0:
        raise ValueError(f"{entity} '{id_value}' not found.")


# ---------------------------------------------------------------------------
# Orchestrator state helpers
# ---------------------------------------------------------------------------


def start_run(
    conn: sqlite3.Connection, project_id: str, ticket_id: str, run_id: str
) -> None:
    """Set active ticket/run and status='running'."""
    cur = conn.execute(
        "UPDATE orchestrator_state "
        "SET active_ticket_id = ?, active_run_id = ?, status = 'running', updated_at = ? "
        "WHERE project_id = ?",
        (ticket_id, run_id, now_utc(), project_id),
    )
    _assert_updated(cur, "Project", project_id)
    conn.commit()


def finish_run(conn: sqlite3.Connection, project_id: str) -> None:
    """Clear active run (keep active ticket)."""
    cur = conn.execute(
        "UPDATE orchestrator_state "
        "SET active_run_id = NULL, updated_at = ? "
        "WHERE project_id = ?",
        (now_utc(), project_id),
    )
    _assert_updated(cur, "Project", project_id)
    conn.commit()


def await_human(conn: sqlite3.Connection, project_id: str) -> None:
    """Set status='awaiting_human'."""
    cur = conn.execute(
        "UPDATE orchestrator_state "
        "SET status = 'awaiting_human', updated_at = ? "
        "WHERE project_id = ?",
        (now_utc(), project_id),
    )
    _assert_updated(cur, "Project", project_id)
    conn.commit()


def set_idle(conn: sqlite3.Connection, project_id: str) -> None:
    """Set status='idle' and clear active ticket/run."""
    cur = conn.execute(
        "UPDATE orchestrator_state "
        "SET status = 'idle', active_ticket_id = NULL, active_run_id = NULL, updated_at = ? "
        "WHERE project_id = ?",
        (now_utc(), project_id),
    )
    _assert_updated(cur, "Project", project_id)
    conn.commit()


def get_state(conn: sqlite3.Connection, project_id: str) -> dict:
    """Return the current orchestrator state as a dict."""
    row = conn.execute(
        "SELECT status, active_ticket_id, active_run_id, suspended_at, resume_context "
        "FROM orchestrator_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No orchestrator state for project '{project_id}'.")
    return {
        "status": row[0],
        "active_ticket_id": row[1],
        "active_run_id": row[2],
        "suspended_at": row[3],
        "resume_context": row[4],
    }


# ---------------------------------------------------------------------------
# Cycle/retry counter helpers (operate on tickets table)
# ---------------------------------------------------------------------------


def init_cycle(conn: sqlite3.Connection, ticket_id: str) -> None:
    """Set cycle=1, impl_attempt=1, review_attempt=1."""
    cur = conn.execute(
        "UPDATE tickets "
        "SET current_cycle = 1, current_impl_attempt = 1, current_review_attempt = 1, "
        "updated_at = ? WHERE id = ?",
        (now_utc(), ticket_id),
    )
    _assert_updated(cur, "Ticket", ticket_id)
    conn.commit()


def increment_cycle(conn: sqlite3.Connection, ticket_id: str) -> None:
    """Increment cycle, reset impl_attempt=1."""
    cur = conn.execute(
        "UPDATE tickets "
        "SET current_cycle = current_cycle + 1, current_impl_attempt = 1, "
        "updated_at = ? WHERE id = ?",
        (now_utc(), ticket_id),
    )
    _assert_updated(cur, "Ticket", ticket_id)
    conn.commit()


def increment_impl_attempt(conn: sqlite3.Connection, ticket_id: str) -> None:
    """Increment implementation attempt counter."""
    cur = conn.execute(
        "UPDATE tickets "
        "SET current_impl_attempt = current_impl_attempt + 1, updated_at = ? "
        "WHERE id = ?",
        (now_utc(), ticket_id),
    )
    _assert_updated(cur, "Ticket", ticket_id)
    conn.commit()


def increment_review_attempt(conn: sqlite3.Connection, ticket_id: str) -> None:
    """Increment review attempt counter."""
    cur = conn.execute(
        "UPDATE tickets "
        "SET current_review_attempt = current_review_attempt + 1, updated_at = ? "
        "WHERE id = ?",
        (now_utc(), ticket_id),
    )
    _assert_updated(cur, "Ticket", ticket_id)
    conn.commit()


def reset_counters(conn: sqlite3.Connection, ticket_id: str) -> None:
    """Reset cycle, impl_attempt, and review_attempt."""
    cur = conn.execute(
        "UPDATE tickets "
        "SET current_cycle = 0, current_impl_attempt = 1, current_review_attempt = 1, "
        "updated_at = ? WHERE id = ?",
        (now_utc(), ticket_id),
    )
    _assert_updated(cur, "Ticket", ticket_id)
    conn.commit()


def check_cycle_limit(
    conn: sqlite3.Connection, ticket_id: str, max_cycles: int
) -> bool:
    """Return True if the ticket has reached or exceeded the cycle limit."""
    row = conn.execute(
        "SELECT current_cycle FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")
    return row[0] >= max_cycles


def check_impl_retry_limit(
    conn: sqlite3.Connection, ticket_id: str, max_retries: int
) -> bool:
    """Return True if the ticket has reached or exceeded the impl retry limit."""
    row = conn.execute(
        "SELECT current_impl_attempt FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")
    return row[0] >= max_retries


def check_review_retry_limit(
    conn: sqlite3.Connection, ticket_id: str, max_retries: int
) -> bool:
    """Return True if the ticket has reached or exceeded the review retry limit."""
    row = conn.execute(
        "SELECT current_review_attempt FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")
    return row[0] >= max_retries
