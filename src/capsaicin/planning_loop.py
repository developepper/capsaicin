"""Automated planning draft-review-revise loop for ``capsaicin plan loop`` (T04).

Executes the planner and planning reviewer pipelines in-process, looping
until the epic reaches ``human-gate`` or ``blocked``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.adapters.base import BaseAdapter
from capsaicin.config import Config
from capsaicin.queries import load_planned_epic
from capsaicin.planning_run import run_draft_pipeline, select_epic_for_draft
from capsaicin.planning_review import run_planning_review_pipeline


def run_planning_loop(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    draft_adapter: BaseAdapter,
    review_adapter: BaseAdapter,
    epic_id: str | None = None,
    max_cycles: int | None = None,
    log_path: str | Path | None = None,
) -> tuple[str, str]:
    """Run the planning draft-review-revise loop.

    Returns a tuple of (final_status, detail) describing the outcome.
    """
    if max_cycles is not None:
        from dataclasses import replace

        config = replace(config, limits=replace(config.limits, max_cycles=max_cycles))

    # Select epic — prefer revise (in-flight) before new
    epic = select_epic_for_draft(conn, project_id, epic_id)
    eid = epic["id"]

    if log_path:
        log_event(
            log_path,
            "PLANNING_LOOP_START",
            project_id=project_id,
            payload={
                "epic_id": eid,
                "max_cycles": config.limits.max_cycles,
            },
        )

    while True:
        epic = load_planned_epic(conn, eid)
        status = epic["status"]

        if status in ("new", "revise"):
            # --- Draft phase ---
            run_draft_pipeline(
                conn=conn,
                project_id=project_id,
                epic=epic,
                config=config,
                adapter=draft_adapter,
                log_path=log_path,
            )
            # Reload and continue — next iteration handles the result
            continue

        if status == "in-review":
            # --- Review phase ---
            run_planning_review_pipeline(
                conn=conn,
                project_id=project_id,
                epic=epic,
                config=config,
                adapter=review_adapter,
                log_path=log_path,
            )
            # Reload and continue — next iteration handles the result
            continue

        # --- Terminal states ---
        if status == "human-gate":
            context = build_planning_human_gate_context(conn, eid)
            if log_path:
                log_event(
                    log_path,
                    "PLANNING_LOOP_STOP",
                    project_id=project_id,
                    payload={
                        "epic_id": eid,
                        "reason": "human-gate",
                        "gate_reason": epic["gate_reason"],
                    },
                )
            return ("human-gate", context)

        if status == "blocked":
            detail = (
                f"Epic {eid} is blocked.\n"
                f"  Blocked Reason: {epic['blocked_reason'] or 'unknown'}"
            )
            if log_path:
                log_event(
                    log_path,
                    "PLANNING_LOOP_STOP",
                    project_id=project_id,
                    payload={
                        "epic_id": eid,
                        "reason": "blocked",
                        "blocked_reason": epic["blocked_reason"],
                    },
                )
            return ("blocked", detail)

        if status in ("approved", "drafting"):
            return (status, f"Epic {eid} is already {status}.")

        # Unexpected status — bail
        return (
            status,
            f"Epic {eid} is in unexpected status '{status}' for planning loop.",
        )


def build_planning_human_gate_context(conn: sqlite3.Connection, epic_id: str) -> str:
    """Build human-gate context display for a planning epic."""
    epic = load_planned_epic(conn, epic_id)

    lines = [
        "Awaiting human decision on planning epic.",
        "",
        f"Epic: {epic['id']}",
        f"  Title: {epic['title'] or '(untitled)'}",
        f"  Status: {epic['status']}",
        f"  Gate Reason: {epic['gate_reason'] or 'unknown'}",
        f"  Cycle: {epic['current_cycle']}",
    ]

    if epic["summary"]:
        lines.append(f"  Summary: {epic['summary']}")

    # Planned tickets
    from capsaicin.queries import load_planned_tickets

    tickets = load_planned_tickets(conn, epic_id)
    lines.append("")
    lines.append(f"Planned Tickets ({len(tickets)}):")
    if tickets:
        for t in tickets:
            lines.append(f"  #{t['sequence']} {t['title']}")
    else:
        lines.append("  (none)")

    # Open findings
    from capsaicin.queries import load_open_planning_findings

    findings = load_open_planning_findings(conn, epic_id)
    lines.append("")
    lines.append("Open Findings:")
    if findings:
        for f in findings:
            lines.append(f"  [{f['severity']}] [{f['category']}] {f['description']}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Available commands:")
    lines.append("  capsaicin plan approve  — approve and materialize tickets")
    lines.append("  capsaicin plan revise   — send back for revision")
    lines.append("  capsaicin plan defer    — defer or abandon")

    return "\n".join(lines)
