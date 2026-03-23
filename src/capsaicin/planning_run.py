"""Planner draft/revise pipeline for planning loop orchestration (T04).

Executes the planner adapter to produce or revise a plan draft, validates
the result, and persists the plan to the database.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from capsaicin.activity_log import build_run_end_payload, log_event
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import PlannerResult, PlanningFinding, RunRequest
from capsaicin.config import Config
from capsaicin.orchestrator import (
    await_planning_human,
    check_draft_retry_limit,
    check_planning_cycle_limit,
    finish_planning_run,
    increment_draft_attempt,
    increment_planning_cycle,
    init_planning_cycle,
    set_planning_idle,
    start_planning_run,
)
from capsaicin.pipeline_outcome import PipelineOutcome
from capsaicin.prompts import build_planner_draft_prompt, build_planner_revise_prompt
from capsaicin.queries import (
    generate_id,
    load_open_planning_findings,
    load_planned_epic,
    now_utc,
)
from capsaicin.state_machine import transition_planned_epic
from capsaicin.validation import validate_planner_result


# ---------------------------------------------------------------------------
# Epic selection
# ---------------------------------------------------------------------------


def select_epic_for_draft(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str | None = None,
) -> dict:
    """Select an epic eligible for drafting (new or revise status).

    If *epic_id* is provided, validate it is in a draftable state.
    Otherwise, auto-select: prefer revise (in-flight) before new.
    """
    from capsaicin.queries import PLANNED_EPIC_COLUMNS

    if epic_id is not None:
        epic = load_planned_epic(conn, epic_id)
        if epic["status"] not in ("new", "revise"):
            raise ValueError(
                f"Epic '{epic_id}' is in '{epic['status']}' status; "
                "expected 'new' or 'revise' for drafting."
            )
        return epic

    # Prefer revise epics (in-flight work)
    row = conn.execute(
        f"SELECT {PLANNED_EPIC_COLUMNS} FROM planned_epics "
        "WHERE project_id = ? AND status = 'revise' "
        "ORDER BY status_changed_at ASC, created_at ASC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is not None:
        return dict(row)

    # Fall back to new epics
    row = conn.execute(
        f"SELECT {PLANNED_EPIC_COLUMNS} FROM planned_epics "
        "WHERE project_id = ? AND status = 'new' "
        "ORDER BY created_at LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            "No epic eligible for drafting (expected status 'new' or 'revise')."
        )
    return dict(row)


# ---------------------------------------------------------------------------
# Run record helpers
# ---------------------------------------------------------------------------


def _insert_planner_run(
    conn: sqlite3.Connection,
    run_id: str,
    epic_id: str,
    cycle_number: int,
    attempt_number: int,
    prompt: str,
    run_request_json: str,
) -> None:
    """Insert an agent_runs row with exit_status='running' for a planner."""
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, epic_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'planner', 'read-write', ?, ?, 'running', ?, ?, ?)",
        (
            run_id,
            epic_id,
            cycle_number,
            attempt_number,
            prompt,
            run_request_json,
            now_utc(),
        ),
    )
    conn.commit()


def _update_planner_run(
    conn: sqlite3.Connection,
    run_id: str,
    exit_status: str,
    duration_seconds: float,
    raw_stdout: str,
    raw_stderr: str,
    adapter_metadata: dict | None,
    structured_result_json: str | None = None,
) -> None:
    """Update an agent_runs row with terminal status and outputs."""
    conn.execute(
        "UPDATE agent_runs SET "
        "exit_status = ?, duration_seconds = ?, "
        "raw_stdout = ?, raw_stderr = ?, "
        "adapter_metadata = ?, structured_result = ?, "
        "finished_at = ? "
        "WHERE id = ?",
        (
            exit_status,
            duration_seconds,
            raw_stdout,
            raw_stderr,
            json.dumps(adapter_metadata or {}),
            structured_result_json,
            now_utc(),
            run_id,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Plan persistence
# ---------------------------------------------------------------------------


def persist_planner_result(
    conn: sqlite3.Connection,
    epic_id: str,
    result: PlannerResult,
) -> None:
    """Persist a validated PlannerResult to the database.

    Updates the epic metadata and replaces all planned tickets,
    criteria, and dependencies.
    """
    now = now_utc()

    # Update epic metadata
    conn.execute(
        "UPDATE planned_epics SET title = ?, summary = ?, success_outcome = ?, "
        "sequencing_notes = ?, updated_at = ? WHERE id = ?",
        (
            result.epic.title,
            result.epic.summary,
            result.epic.success_outcome,
            result.sequencing_notes,
            now,
            epic_id,
        ),
    )

    # Delete existing planned tickets (cascade via FK would be ideal, but
    # SQLite foreign_keys may not cascade, so delete explicitly in order)
    existing_ticket_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM planned_tickets WHERE epic_id = ?", (epic_id,)
        ).fetchall()
    ]
    for tid in existing_ticket_ids:
        conn.execute(
            "DELETE FROM planned_ticket_dependencies WHERE planned_ticket_id = ?",
            (tid,),
        )
        conn.execute(
            "DELETE FROM planned_ticket_dependencies WHERE depends_on_id = ?",
            (tid,),
        )
        conn.execute(
            "DELETE FROM planned_ticket_criteria WHERE planned_ticket_id = ?",
            (tid,),
        )
    conn.execute("DELETE FROM planned_tickets WHERE epic_id = ?", (epic_id,))

    # Insert new planned tickets
    seq_to_id: dict[int, str] = {}
    for ticket in result.tickets:
        ticket_id = generate_id()
        seq_to_id[ticket.sequence] = ticket_id
        conn.execute(
            "INSERT INTO planned_tickets "
            "(id, epic_id, sequence, title, goal, scope, non_goals, "
            "references_, implementation_notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                epic_id,
                ticket.sequence,
                ticket.title,
                ticket.goal,
                "\n".join(ticket.scope),
                "\n".join(ticket.non_goals),
                "\n".join(ticket.references),
                "\n".join(ticket.implementation_notes),
                now,
                now,
            ),
        )

        # Insert acceptance criteria
        for criterion in ticket.acceptance_criteria:
            crit_id = generate_id()
            conn.execute(
                "INSERT INTO planned_ticket_criteria "
                "(id, planned_ticket_id, description) VALUES (?, ?, ?)",
                (crit_id, ticket_id, criterion.description),
            )

    # Insert dependencies (second pass, after all tickets have IDs)
    for ticket in result.tickets:
        ticket_id = seq_to_id[ticket.sequence]
        for dep_seq in ticket.dependencies:
            dep_id = seq_to_id.get(dep_seq)
            if dep_id:
                conn.execute(
                    "INSERT INTO planned_ticket_dependencies "
                    "(planned_ticket_id, depends_on_id) VALUES (?, ?)",
                    (ticket_id, dep_id),
                )

    conn.commit()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_draft_pipeline(
    conn: sqlite3.Connection,
    project_id: str,
    epic: dict,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> str:
    """Execute the planner draft/revise pipeline for an epic.

    Returns the final epic status after the pipeline completes.
    """
    epic_id = epic["id"]
    from_status = epic["status"]

    # --- Cycle-limit shortcut (revise only) ---
    if from_status == "revise":
        if check_planning_cycle_limit(conn, epic_id, config.limits.max_cycles):
            transition_planned_epic(
                conn,
                epic_id,
                "human-gate",
                "system",
                reason="Cycle limit reached before re-drafting.",
                gate_reason="cycle_limit",
                log_path=log_path,
            )
            await_planning_human(conn, project_id)
            if log_path:
                log_event(
                    log_path,
                    "PLANNING_CYCLE_LIMIT",
                    project_id=project_id,
                    payload={
                        "epic_id": epic_id,
                        "max_cycles": config.limits.max_cycles,
                    },
                )
            return "human-gate"

    # --- Transition to drafting ---
    transition_planned_epic(
        conn,
        epic_id,
        "drafting",
        "system",
        reason="Starting planner draft run.",
        log_path=log_path,
    )

    # --- Cycle management ---
    if from_status == "new":
        init_planning_cycle(conn, epic_id)
    elif from_status == "revise":
        increment_planning_cycle(conn, epic_id)

    # Reload epic to get updated counters
    epic_row = conn.execute(
        "SELECT current_cycle, current_draft_attempt FROM planned_epics WHERE id = ?",
        (epic_id,),
    ).fetchone()
    cycle_number = epic_row["current_cycle"]
    attempt_number = epic_row["current_draft_attempt"]

    # --- Invoke adapter (with retry loop) ---
    return invoke_draft_with_retries(
        conn=conn,
        project_id=project_id,
        epic_id=epic_id,
        cycle_number=cycle_number,
        attempt_number=attempt_number,
        config=config,
        adapter=adapter,
        log_path=log_path,
    )


def invoke_draft_with_retries(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    cycle_number: int,
    attempt_number: int,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> str:
    """Invoke the planner adapter, handling retries on failure/timeout.

    Returns the final epic status.
    """
    while True:
        # Reload attempt number
        epic_row = conn.execute(
            "SELECT current_draft_attempt FROM planned_epics WHERE id = ?",
            (epic_id,),
        ).fetchone()
        attempt_number = epic_row["current_draft_attempt"]

        outcome = _draft_invoke_once(
            conn=conn,
            project_id=project_id,
            epic_id=epic_id,
            cycle_number=cycle_number,
            attempt_number=attempt_number,
            config=config,
            adapter=adapter,
            log_path=log_path,
        )

        if not outcome.should_retry:
            return outcome.status

        # Check retry limit before looping
        if check_draft_retry_limit(conn, epic_id, config.limits.max_impl_retries):
            transition_planned_epic(
                conn,
                epic_id,
                "blocked",
                "system",
                reason="Draft retry limit exceeded.",
                blocked_reason="draft_failure",
                log_path=log_path,
            )
            finish_planning_run(conn, project_id)
            set_planning_idle(conn, project_id)
            if log_path:
                log_event(
                    log_path,
                    "PLANNING_RETRY_LIMIT",
                    project_id=project_id,
                    payload={
                        "epic_id": epic_id,
                        "role": "planner",
                        "max_retries": config.limits.max_impl_retries,
                    },
                )
            return "blocked"

        # Increment attempt and loop
        increment_draft_attempt(conn, epic_id)


def _draft_invoke_once(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    cycle_number: int,
    attempt_number: int,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> PipelineOutcome:
    """Single planner invocation. Returns a PipelineOutcome."""
    run_id = generate_id()

    # Load context
    epic = load_planned_epic(conn, epic_id)
    prior_findings = load_open_planning_findings(conn, epic_id)

    # Build prompt based on whether this is a fresh draft or revision
    if cycle_number == 1 and epic["title"] is None:
        # Fresh draft
        prompt = build_planner_draft_prompt(
            problem_statement=epic["problem_statement"],
        )
    else:
        # Revision — load current plan draft for context
        plan_draft = _build_plan_draft_dict(conn, epic)
        finding_objects = [
            PlanningFinding(
                severity=f["severity"],
                category=f["category"],
                description=f["description"],
                target_type="epic",
            )
            for f in prior_findings
        ]
        prompt = build_planner_revise_prompt(
            problem_statement=epic["problem_statement"],
            plan_draft=plan_draft,
            prior_findings=finding_objects,
            cycle=cycle_number,
            max_cycles=config.limits.max_cycles,
        )

    run_request = RunRequest(
        run_id=run_id,
        role="planner",
        mode="read-write",
        working_directory=config.project.repo_path,
        prompt=prompt,
        timeout_seconds=config.limits.timeout_seconds,
        adapter_config={
            "backend": config.implementer.backend,
            "command": config.implementer.command,
        },
    )

    # Insert run record
    _insert_planner_run(
        conn,
        run_id,
        epic_id,
        cycle_number,
        attempt_number,
        prompt,
        run_request.to_json(),
    )

    # Update orchestrator state
    start_planning_run(conn, project_id, epic_id, run_id)

    if log_path:
        log_event(
            log_path,
            "RUN_START",
            project_id=project_id,
            run_id=run_id,
            payload={
                "epic_id": epic_id,
                "role": "planner",
                "cycle": cycle_number,
                "attempt": attempt_number,
            },
        )

    # Invoke adapter
    result = adapter.execute(run_request)

    # Update run record
    structured_json = (
        result.structured_result.to_json() if result.structured_result else None
    )
    _update_planner_run(
        conn,
        run_id,
        exit_status=result.exit_status,
        duration_seconds=result.duration_seconds,
        raw_stdout=result.raw_stdout,
        raw_stderr=result.raw_stderr,
        adapter_metadata=result.adapter_metadata,
        structured_result_json=structured_json,
    )

    if log_path:
        log_event(
            log_path,
            "RUN_END",
            project_id=project_id,
            run_id=run_id,
            payload=build_run_end_payload(
                result.exit_status,
                result.duration_seconds,
                result.adapter_metadata,
            ),
        )

    # Handle result
    return _handle_draft_result(
        conn=conn,
        project_id=project_id,
        epic_id=epic_id,
        run_id=run_id,
        result=result,
        log_path=log_path,
    )


def _handle_draft_result(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    run_id: str,
    result,
    log_path: str | Path | None = None,
) -> PipelineOutcome:
    """Process the planner result and transition the epic."""
    # Permission denied — route to human-gate
    if result.exit_status == "permission_denied":
        transition_planned_epic(
            conn,
            epic_id,
            "human-gate",
            "system",
            reason="Planner run blocked by permission denials.",
            gate_reason="draft_failure",
            log_path=log_path,
        )
        finish_planning_run(conn, project_id)
        await_planning_human(conn, project_id)
        return PipelineOutcome.terminal("human-gate")

    # Failure or timeout — signal retry
    if result.exit_status in ("failure", "timeout"):
        return PipelineOutcome.retry(result.exit_status)

    # Parse error
    if result.exit_status == "parse_error":
        return PipelineOutcome.retry("parse_error")

    # Success — validate and persist
    if result.exit_status != "success":
        return PipelineOutcome.retry(result.exit_status)

    planner_result = result.structured_result
    if planner_result is None or not isinstance(planner_result, PlannerResult):
        return PipelineOutcome.retry("parse_error")

    # Validate
    validation = validate_planner_result(planner_result.to_dict())
    if not validation.is_valid:
        if log_path:
            log_event(
                log_path,
                "VALIDATION_FAILURE",
                project_id=project_id,
                run_id=run_id,
                payload={
                    "epic_id": epic_id,
                    "violations": validation.violations,
                },
            )
        return PipelineOutcome.retry("parse_error")

    # Persist
    persist_planner_result(conn, epic_id, planner_result)

    # Transition to in-review
    transition_planned_epic(
        conn,
        epic_id,
        "in-review",
        "system",
        reason="Planner draft complete, ready for review.",
        log_path=log_path,
    )
    finish_planning_run(conn, project_id)
    set_planning_idle(conn, project_id)
    return PipelineOutcome.terminal("in-review")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_plan_draft_dict(conn: sqlite3.Connection, epic: dict) -> dict:
    """Build a plan draft dict from the current DB state for revision prompts."""
    from capsaicin.queries import load_planned_ticket_criteria, load_planned_tickets

    tickets = load_planned_tickets(conn, epic["id"])
    ticket_dicts = []
    for t in tickets:
        criteria = load_planned_ticket_criteria(conn, t["id"])
        ticket_dicts.append(
            {
                "sequence": t["sequence"],
                "title": t["title"],
                "goal": t["goal"],
                "scope": t["scope"].split("\n") if t["scope"] else [],
                "non_goals": t["non_goals"].split("\n") if t["non_goals"] else [],
                "acceptance_criteria": [
                    {"description": c["description"]} for c in criteria
                ],
                "dependencies": [],  # Not critical for revision prompt
                "references": (
                    t["references_"].split("\n") if t["references_"] else []
                ),
                "implementation_notes": (
                    t["implementation_notes"].split("\n")
                    if t["implementation_notes"]
                    else []
                ),
            }
        )

    return {
        "epic": {
            "title": epic["title"] or "",
            "summary": epic["summary"] or "",
            "success_outcome": epic["success_outcome"] or "",
        },
        "tickets": ticket_dicts,
        "sequencing_notes": epic["sequencing_notes"],
        "open_questions": [],
    }
