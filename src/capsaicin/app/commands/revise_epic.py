"""Command service for ``plan revise``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult


def _select_revisable_epic(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
) -> dict:
    """Select an epic eligible for revision (human-gate -> revise).

    If *epic_id* is provided, validate it is in ``human-gate`` status.
    Otherwise, auto-select the first epic in ``human-gate`` status.
    """
    from capsaicin.queries import PLANNED_EPIC_COLUMNS, load_planned_epic

    if epic_id is not None:
        epic = load_planned_epic(conn, epic_id)
        if epic["status"] != "human-gate":
            raise ValueError(
                f"Epic '{epic_id}' is in '{epic['status']}' status; "
                "expected 'human-gate' for revision."
            )
        return epic

    row = conn.execute(
        f"SELECT {PLANNED_EPIC_COLUMNS} FROM planned_epics "
        "WHERE project_id = ? AND status = 'human-gate' "
        "ORDER BY status_changed_at LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            "No epic eligible for revision (expected status 'human-gate')."
        )
    return dict(row)


def revise(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
    add_findings: list[str] | None = None,
    log_path: str | Path | None = None,
) -> PlanningCommandResult:
    """Send an epic back for revision from the human gate.

    Optionally adds human findings before transitioning.

    Returns a structured ``PlanningCommandResult``.
    """
    from capsaicin.state_machine import transition_planned_epic

    epic = _select_revisable_epic(conn, project_id, epic_id)

    if add_findings:
        _create_human_run_with_findings(conn, epic["id"], add_findings)

    transition_planned_epic(
        conn,
        epic["id"],
        "revise",
        "human",
        reason="human revision request",
        log_path=log_path,
    )

    detail = f"Epic {epic['id']} sent back for revision"
    if add_findings:
        detail += f" with {len(add_findings)} finding(s)"

    return PlanningCommandResult(
        epic_id=epic["id"],
        final_status="revise",
        detail=detail,
    )


def _create_human_run_with_findings(
    conn: sqlite3.Connection,
    epic_id: str,
    finding_descriptions: list[str],
) -> str:
    """Create a synthetic human agent_run and attach planning findings."""
    from capsaicin.queries import generate_id, now_utc

    run_id = generate_id()
    now = now_utc()

    row = conn.execute(
        "SELECT current_cycle FROM planned_epics WHERE id = ?", (epic_id,)
    ).fetchone()
    cycle_number = row["current_cycle"]

    conn.execute(
        "INSERT INTO agent_runs "
        "(id, epic_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, duration_seconds, "
        "started_at, finished_at) "
        "VALUES (?, ?, 'human', 'read-write', ?, 1, 'success', "
        "'human feedback via plan revise', '{}', 0.0, ?, ?)",
        (run_id, epic_id, cycle_number, now, now),
    )

    for desc in finding_descriptions:
        finding_id = generate_id()
        conn.execute(
            "INSERT INTO planning_findings "
            "(id, run_id, epic_id, severity, category, description, "
            "fingerprint, disposition) "
            "VALUES (?, ?, ?, 'warning', 'human', ?, ?, 'open')",
            (finding_id, run_id, epic_id, desc, f"human-{finding_id}"),
        )

    conn.commit()
    return run_id
