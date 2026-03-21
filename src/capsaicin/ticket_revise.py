"""Human-initiated revision for ``capsaicin ticket revise`` (T22).

Transitions a ticket from human-gate back to revise, optionally adding
human-supplied findings and resetting cycle counters.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.orchestrator import reset_counters, set_idle
from capsaicin.reconciliation import compute_fingerprint
from capsaicin.state_machine import transition_ticket


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id() -> str:
    from ulid import ULID

    return str(ULID())


# ---------------------------------------------------------------------------
# Ticket selection
# ---------------------------------------------------------------------------


def select_revise_ticket(
    conn: sqlite3.Connection, ticket_id: str | None = None
) -> dict:
    """Select a ticket for revision.

    If *ticket_id* is given, validate that it exists and is in ``human-gate``.
    Otherwise auto-select the first ``human-gate`` ticket ordered by
    ``status_changed_at``.

    Returns a dict with ticket row data.
    Raises ``ValueError`` if no eligible ticket is found.
    """
    from capsaicin.queries import TICKET_COLUMNS, load_ticket

    from capsaicin.errors import InvalidStatusError, NoEligibleTicketError

    if ticket_id:
        ticket = load_ticket(conn, ticket_id)
        if ticket["status"] != "human-gate":
            raise InvalidStatusError(ticket_id, ticket["status"], "human-gate")
        return ticket

    row = conn.execute(
        f"SELECT {TICKET_COLUMNS} "
        "FROM tickets WHERE status = 'human-gate' "
        "ORDER BY status_changed_at"
    ).fetchone()

    if row is None:
        raise NoEligibleTicketError("No ticket found in 'human-gate' status.")

    return dict(row)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def revise_ticket(
    conn: sqlite3.Connection,
    project_id: str,
    ticket: dict,
    add_findings: list[str] | None = None,
    reset_cycles: bool = False,
    log_path: str | Path | None = None,
) -> str:
    """Execute the revision pipeline for a ticket.

    Returns the final ticket status ('revise').
    """
    ticket_id = ticket["id"]
    now = _now()

    # --- Create synthetic human run and findings if requested ---
    if add_findings:
        _create_human_run_with_findings(conn, ticket_id, add_findings, now)

    # --- Record decision ---
    decision_id = _generate_id()
    conn.execute(
        "INSERT INTO decisions (id, ticket_id, decision, rationale, created_at) "
        "VALUES (?, ?, 'revise', NULL, ?)",
        (decision_id, ticket_id, now),
    )
    conn.commit()

    # --- Transition to revise ---
    transition_ticket(
        conn,
        ticket_id,
        "revise",
        "human",
        reason="Human requested revision.",
        log_path=log_path,
    )

    # --- Reset counters if requested ---
    if reset_cycles:
        reset_counters(conn, ticket_id)

    # --- Set orchestrator to idle ---
    set_idle(conn, project_id)

    if log_path:
        log_event(
            log_path,
            "DECISION",
            project_id=project_id,
            ticket_id=ticket_id,
            payload={
                "decision": "revise",
                "reset_cycles": reset_cycles,
                "findings_added": len(add_findings) if add_findings else 0,
            },
        )

    return "revise"


def _create_human_run_with_findings(
    conn: sqlite3.Connection,
    ticket_id: str,
    finding_descriptions: list[str],
    now: str,
) -> str:
    """Create a synthetic human agent_run and attach findings to it."""
    run_id = _generate_id()

    # Get current cycle number
    row = conn.execute(
        "SELECT current_cycle FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    cycle_number = row["current_cycle"]

    # Insert synthetic agent_run
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, duration_seconds, "
        "started_at, finished_at) "
        "VALUES (?, ?, 'human', 'read-write', ?, 1, 'success', "
        "'human feedback via ticket revise', '{}', 0.0, ?, ?)",
        (run_id, ticket_id, cycle_number, now, now),
    )

    # Insert findings
    for desc in finding_descriptions:
        finding_id = _generate_id()
        fp = compute_fingerprint("human_feedback", None, desc)
        conn.execute(
            "INSERT INTO findings "
            "(id, run_id, ticket_id, severity, category, location, "
            "fingerprint, description, disposition, created_at, updated_at) "
            "VALUES (?, ?, ?, 'blocking', 'human_feedback', NULL, "
            "?, ?, 'open', ?, ?)",
            (finding_id, run_id, ticket_id, fp, desc, now, now),
        )

    conn.commit()
    return run_id
