"""Logic for `capsaicin ticket dep`."""

from __future__ import annotations

import sqlite3
from collections import deque


def _ticket_exists(conn: sqlite3.Connection, ticket_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return row is not None


def _would_create_cycle(
    conn: sqlite3.Connection, ticket_id: str, depends_on_id: str
) -> bool:
    """BFS from depends_on_id's own dependencies to see if ticket_id is reachable."""
    visited: set[str] = set()
    queue: deque[str] = deque([depends_on_id])
    while queue:
        current = queue.popleft()
        if current == ticket_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        rows = conn.execute(
            "SELECT depends_on_id FROM ticket_dependencies WHERE ticket_id = ?",
            (current,),
        ).fetchall()
        for row in rows:
            queue.append(row[0])
    return False


def add_dependency(
    conn: sqlite3.Connection, ticket_id: str, depends_on_id: str
) -> None:
    """Add a dependency edge. Validates existence, self-dep, and cycles."""
    if ticket_id == depends_on_id:
        raise ValueError("A ticket cannot depend on itself.")

    if not _ticket_exists(conn, ticket_id):
        raise ValueError(f"Ticket '{ticket_id}' not found.")
    if not _ticket_exists(conn, depends_on_id):
        raise ValueError(f"Ticket '{depends_on_id}' not found.")

    # Check for duplicate
    existing = conn.execute(
        "SELECT 1 FROM ticket_dependencies WHERE ticket_id = ? AND depends_on_id = ?",
        (ticket_id, depends_on_id),
    ).fetchone()
    if existing:
        return  # idempotent

    # Cycle detection
    if _would_create_cycle(conn, ticket_id, depends_on_id):
        raise ValueError(
            f"Adding dependency {ticket_id} -> {depends_on_id} would create a cycle."
        )

    conn.execute(
        "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
        (ticket_id, depends_on_id),
    )
    conn.commit()
