"""Command service for ``plan materialize``.

Standalone re-materialization of an approved epic.  Useful after plan
revisions or when ``--force`` is needed to overwrite manually edited docs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.app.commands import PlanningCommandResult


def materialize(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    repo_root: Path,
    force: bool = False,
    log_path: str | Path | None = None,
) -> PlanningCommandResult:
    """(Re-)materialize an approved epic.

    Returns a ``PlanningCommandResult`` with a detail string summarising
    docs written, tickets created, and any conflicts.
    """
    from capsaicin.materialize import materialize_epic

    mat = materialize_epic(
        conn=conn,
        project_id=project_id,
        epic_id=epic_id,
        repo_root=repo_root,
        force=force,
        log_path=log_path,
    )

    parts = [
        f"Materialized epic {epic_id}: "
        f"{mat.docs_written} docs, {mat.tickets_created} tickets"
    ]
    if mat.conflicts:
        conflict_files = ", ".join(c.file_path for c in mat.conflicts)
        parts.append(f". Conflicts (pass --force to overwrite): {conflict_files}")

    return PlanningCommandResult(
        epic_id=epic_id,
        final_status="approved",
        detail="".join(parts),
    )
