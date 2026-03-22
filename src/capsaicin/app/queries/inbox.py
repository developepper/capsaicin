"""Human-gate inbox read model — tickets awaiting human decisions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class InboxItem:
    """A ticket in the human-gate awaiting a decision."""

    ticket_id: str
    title: str
    gate_reason: str | None = None
    status_changed_at: str | None = None
    criteria: list[dict] = field(default_factory=list)
    open_findings: list[dict] = field(default_factory=list)


def get_inbox(conn: sqlite3.Connection, project_id: str) -> list[InboxItem]:
    """Return all human-gate tickets with criteria and open findings."""
    tickets = conn.execute(
        "SELECT id, title, gate_reason, status_changed_at "
        "FROM tickets "
        "WHERE project_id = ? AND status = 'human-gate' "
        "ORDER BY status_changed_at",
        (project_id,),
    ).fetchall()

    items: list[InboxItem] = []
    for t in tickets:
        tid = t["id"]

        criteria = conn.execute(
            "SELECT id, description, status "
            "FROM acceptance_criteria WHERE ticket_id = ? ORDER BY id",
            (tid,),
        ).fetchall()

        findings = conn.execute(
            "SELECT id, severity, category, location, description "
            "FROM findings WHERE ticket_id = ? AND disposition = 'open' "
            "ORDER BY severity, created_at",
            (tid,),
        ).fetchall()

        items.append(
            InboxItem(
                ticket_id=tid,
                title=t["title"],
                gate_reason=t["gate_reason"],
                status_changed_at=t["status_changed_at"],
                criteria=[dict(c) for c in criteria],
                open_findings=[dict(f) for f in findings],
            )
        )

    return items
