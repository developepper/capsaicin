"""Diagnostics read model — structured run diagnostic data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from capsaicin.diagnostics import (
    denial_summary,
    extract_result_text_from_raw,
)


@dataclass
class RunDiagnostic:
    """Structured diagnostic for a single agent run."""

    run_id: str
    role: str
    exit_status: str
    gate_reason: str | None = None
    denial_summary: str | None = None
    agent_text: str | None = None
    duration_seconds: float | None = None


def get_run_diagnostic(
    conn: sqlite3.Connection,
    ticket_id: str,
    run_id: str | None = None,
) -> RunDiagnostic | None:
    """Build a structured diagnostic for a run.

    If *run_id* is not provided, uses the most recent run for the ticket.
    Returns ``None`` if no run is found.
    """
    import json

    if run_id:
        row = conn.execute(
            "SELECT id, role, exit_status, raw_stdout, adapter_metadata, "
            "duration_seconds "
            "FROM agent_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, role, exit_status, raw_stdout, adapter_metadata, "
            "duration_seconds "
            "FROM agent_runs WHERE ticket_id = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()

    if row is None:
        return None

    meta_json = row["adapter_metadata"] or "{}"
    try:
        meta = json.loads(meta_json)
    except (json.JSONDecodeError, TypeError):
        meta = {}

    gate_row = conn.execute(
        "SELECT gate_reason FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    gate_reason = gate_row["gate_reason"] if gate_row else None

    agent_text = extract_result_text_from_raw(row["raw_stdout"])

    return RunDiagnostic(
        run_id=row["id"],
        role=row["role"],
        exit_status=row["exit_status"],
        gate_reason=gate_reason,
        denial_summary=denial_summary(meta) or None,
        agent_text=agent_text or None,
        duration_seconds=row["duration_seconds"],
    )
