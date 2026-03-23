"""Command service for ``plan approve``.

Approval triggers materialization as a side-effect when a ``repo_root``
is provided (T05).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult


def _select_approvable_epic(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
) -> dict:
    """Select an epic eligible for approval (human-gate -> approved).

    If *epic_id* is provided, validate it is in ``human-gate`` status.
    Otherwise, auto-select the first epic in ``human-gate`` status.
    """
    from capsaicin.queries import PLANNED_EPIC_COLUMNS, load_planned_epic

    if epic_id is not None:
        epic = load_planned_epic(conn, epic_id)
        if epic["status"] != "human-gate":
            raise ValueError(
                f"Epic '{epic_id}' is in '{epic['status']}' status; "
                "expected 'human-gate' for approval."
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
            "No epic eligible for approval (expected status 'human-gate')."
        )
    return dict(row)


def approve(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
    rationale: str | None = None,
    log_path: str | Path | None = None,
    repo_root: Path | None = None,
    force: bool = False,
) -> PlanningCommandResult:
    """Approve an epic at the human gate.

    Records a decision, transitions to ``approved``, and materializes
    the plan into implementation tickets when *repo_root* is provided.

    Returns a structured ``PlanningCommandResult``.
    """
    from capsaicin.queries import generate_id
    from capsaicin.state_machine import transition_planned_epic
    from capsaicin.materialize import materialize_epic

    epic = _select_approvable_epic(conn, project_id, epic_id)

    # Materialize as approval side-effect
    detail = f"Epic {epic['id']} approved"
    if repo_root is not None:
        mat = materialize_epic(
            conn=conn,
            project_id=project_id,
            epic_id=epic["id"],
            repo_root=repo_root,
            force=force,
            log_path=log_path,
            allowed_statuses=("human-gate",),
        )
        if mat.conflicts:
            conflict_files = ", ".join(c.file_path for c in mat.conflicts)
            raise ValueError(
                "Materialization blocked by manual edits: "
                f"{conflict_files}. Pass --force to overwrite."
            )

        parts = [detail]
        parts.append(
            f"; materialized {mat.docs_written} docs, {mat.tickets_created} tickets"
        )
        detail = "".join(parts)

    conn.execute(
        "INSERT INTO decisions "
        "(id, epic_id, decision, rationale) "
        "VALUES (?, ?, 'approve', ?)",
        (generate_id(), epic["id"], rationale),
    )

    transition_planned_epic(
        conn,
        epic["id"],
        "approved",
        "human",
        reason=rationale or "human approval",
        log_path=log_path,
    )

    return PlanningCommandResult(
        epic_id=epic["id"],
        final_status="approved",
        detail=detail,
    )
