"""Planning reviewer pipeline for planning loop orchestration (T04).

Executes the planning reviewer adapter to evaluate a plan draft,
validates the result, and reconciles planning findings.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from capsaicin.activity_log import build_run_end_payload, log_event
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import PlanningReviewResult, RunRequest
from capsaicin.config import Config
from capsaicin.orchestrator import (
    await_planning_human,
    check_planning_cycle_limit,
    check_planning_review_retry_limit,
    finish_planning_run,
    increment_planning_review_attempt,
    set_planning_idle,
    start_planning_run,
)
from capsaicin.pipeline_outcome import PipelineOutcome
from capsaicin.prompts import build_planning_reviewer_prompt
from capsaicin.queries import (
    generate_id,
    load_open_planning_findings,
    load_planned_epic,
    load_planned_tickets,
    now_utc,
)
from capsaicin.state_machine import transition_planned_epic
from capsaicin.validation import validate_planning_review_result


# ---------------------------------------------------------------------------
# Run record helpers
# ---------------------------------------------------------------------------


def _insert_planning_reviewer_run(
    conn: sqlite3.Connection,
    run_id: str,
    epic_id: str,
    cycle_number: int,
    attempt_number: int,
    prompt: str,
    run_request_json: str,
) -> None:
    """Insert an agent_runs row with exit_status='running' for a planning reviewer."""
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, epic_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'reviewer', 'read-only', ?, ?, 'running', ?, ?, ?)",
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


def _update_planning_reviewer_run(
    conn: sqlite3.Connection,
    run_id: str,
    exit_status: str,
    duration_seconds: float,
    raw_stdout: str,
    raw_stderr: str,
    adapter_metadata: dict | None,
    structured_result_json: str | None = None,
    verdict: str | None = None,
) -> None:
    """Update an agent_runs row with terminal status and outputs."""
    conn.execute(
        "UPDATE agent_runs SET "
        "exit_status = ?, duration_seconds = ?, "
        "raw_stdout = ?, raw_stderr = ?, "
        "adapter_metadata = ?, structured_result = ?, "
        "verdict = ?, finished_at = ? "
        "WHERE id = ?",
        (
            exit_status,
            duration_seconds,
            raw_stdout,
            raw_stderr,
            json.dumps(adapter_metadata or {}),
            structured_result_json,
            verdict,
            now_utc(),
            run_id,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Finding reconciliation
# ---------------------------------------------------------------------------


def reconcile_planning_findings(
    conn: sqlite3.Connection,
    epic_id: str,
    review_run_id: str,
    new_findings: list,
    verdict: str,
    is_first_cycle: bool,
) -> None:
    """Reconcile planning findings from a review run.

    On the first cycle, all findings are new. On subsequent cycles,
    existing findings with matching fingerprints are updated and
    new ones are inserted.
    """
    now = now_utc()

    if is_first_cycle:
        # First cycle: mark all previous findings as fixed
        conn.execute(
            "UPDATE planning_findings SET disposition = 'fixed', "
            "resolved_in_run = ?, updated_at = ? "
            "WHERE epic_id = ? AND disposition = 'open'",
            (review_run_id, now, epic_id),
        )

    # Insert new findings
    for finding in new_findings:
        planned_ticket_id = None
        if finding.target_type == "ticket" and finding.target_sequence is not None:
            ticket_row = conn.execute(
                "SELECT id FROM planned_tickets WHERE epic_id = ? AND sequence = ?",
                (epic_id, finding.target_sequence),
            ).fetchone()
            if ticket_row:
                planned_ticket_id = ticket_row["id"]

        target_sequence = (
            finding.target_sequence if finding.target_type == "ticket" else None
        )
        fingerprint = (
            f"{finding.category}:{finding.target_type}:{target_sequence}:"
            f"{finding.description[:80]}"
        )

        # Check for existing finding with same fingerprint
        existing = conn.execute(
            "SELECT id FROM planning_findings "
            "WHERE epic_id = ? AND fingerprint = ? AND disposition = 'open'",
            (epic_id, fingerprint),
        ).fetchone()

        if existing:
            # Update existing finding
            conn.execute(
                "UPDATE planning_findings SET "
                "planned_ticket_id = ?, severity = ?, description = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    planned_ticket_id,
                    finding.severity,
                    finding.description,
                    now,
                    existing["id"],
                ),
            )
        else:
            finding_id = generate_id()
            conn.execute(
                "INSERT INTO planning_findings "
                "(id, run_id, epic_id, planned_ticket_id, severity, category, "
                "description, fingerprint, disposition, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (
                    finding_id,
                    review_run_id,
                    epic_id,
                    planned_ticket_id,
                    finding.severity,
                    finding.category,
                    finding.description,
                    fingerprint,
                    now,
                    now,
                ),
            )

    # If verdict is pass, mark remaining open findings as fixed
    if verdict == "pass":
        conn.execute(
            "UPDATE planning_findings SET disposition = 'fixed', "
            "resolved_in_run = ?, updated_at = ? "
            "WHERE epic_id = ? AND disposition = 'open'",
            (review_run_id, now, epic_id),
        )

    conn.commit()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_planning_review_pipeline(
    conn: sqlite3.Connection,
    project_id: str,
    epic: dict,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> str:
    """Execute the planning review pipeline for an epic.

    Returns the final epic status after the pipeline completes.
    """
    epic_id = epic["id"]

    return invoke_planning_review_with_retries(
        conn=conn,
        project_id=project_id,
        epic_id=epic_id,
        config=config,
        adapter=adapter,
        log_path=log_path,
    )


def invoke_planning_review_with_retries(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> str:
    """Invoke the planning reviewer adapter, handling retries.

    Returns the final epic status.
    """
    while True:
        # Reload attempt number
        epic_row = conn.execute(
            "SELECT current_review_attempt FROM planned_epics WHERE id = ?",
            (epic_id,),
        ).fetchone()
        attempt_number = epic_row["current_review_attempt"]

        outcome = _planning_review_invoke_once(
            conn=conn,
            project_id=project_id,
            epic_id=epic_id,
            attempt_number=attempt_number,
            config=config,
            adapter=adapter,
            log_path=log_path,
        )

        if not outcome.should_retry:
            return outcome.status

        last_retry_reason = outcome.retry_reason or "unknown"

        # Check retry limit before looping
        if check_planning_review_retry_limit(
            conn, epic_id, config.limits.max_review_retries
        ):
            transition_planned_epic(
                conn,
                epic_id,
                "blocked",
                "system",
                reason=f"Planning review retry limit exceeded ({last_retry_reason}).",
                blocked_reason=f"reviewer_{last_retry_reason}",
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
                        "role": "planning_reviewer",
                        "max_retries": config.limits.max_review_retries,
                        "last_failure": last_retry_reason,
                    },
                )
            return "blocked"

        # Increment attempt and loop
        increment_planning_review_attempt(conn, epic_id)


def _planning_review_invoke_once(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    attempt_number: int,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> PipelineOutcome:
    """Single planning reviewer invocation. Returns a PipelineOutcome."""
    run_id = generate_id()

    # Load context
    epic = load_planned_epic(conn, epic_id)
    prior_findings_rows = load_open_planning_findings(conn, epic_id)
    cycle_number = epic["current_cycle"]

    # Build plan draft dict for the reviewer prompt
    from capsaicin.planning_run import _build_plan_draft_dict

    plan_draft = _build_plan_draft_dict(conn, epic)

    # Convert prior findings to PlanningFinding objects for the prompt
    from capsaicin.adapters.types import PlanningFinding as PF

    prior_finding_objects = (
        [
            PF(
                severity=f["severity"],
                category=f["category"],
                description=f["description"],
                target_type=f["target_type"],
                target_sequence=f["target_sequence"],
            )
            for f in prior_findings_rows
        ]
        if prior_findings_rows
        else None
    )

    prompt = build_planning_reviewer_prompt(
        problem_statement=epic["problem_statement"],
        plan_draft=plan_draft,
        prior_findings=prior_finding_objects,
    )

    run_request = RunRequest(
        run_id=run_id,
        role="reviewer",
        mode="read-only",
        working_directory=config.project.repo_path,
        prompt=prompt,
        timeout_seconds=config.limits.timeout_seconds,
        adapter_config={
            "backend": config.reviewer.backend,
            "command": config.reviewer.command,
            "allowed_tools": config.reviewer.allowed_tools,
        },
    )

    # Insert run record
    _insert_planning_reviewer_run(
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
                "role": "planning_reviewer",
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
    _update_planning_reviewer_run(
        conn,
        run_id,
        exit_status=result.exit_status,
        duration_seconds=result.duration_seconds,
        raw_stdout=result.raw_stdout,
        raw_stderr=result.raw_stderr,
        adapter_metadata=result.adapter_metadata,
        structured_result_json=structured_json,
        verdict=(
            result.structured_result.verdict if result.structured_result else None
        ),
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
    return _handle_planning_review_result(
        conn=conn,
        project_id=project_id,
        epic_id=epic_id,
        run_id=run_id,
        result=result,
        config=config,
        log_path=log_path,
    )


def _handle_planning_review_result(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    run_id: str,
    result,
    config: Config,
    log_path: str | Path | None = None,
) -> PipelineOutcome:
    """Process the planning reviewer result and transition the epic."""
    # Permission denied
    if result.exit_status == "permission_denied":
        transition_planned_epic(
            conn,
            epic_id,
            "human-gate",
            "system",
            reason="Planning reviewer run blocked by permission denials.",
            gate_reason="reviewer_escalated",
            log_path=log_path,
        )
        finish_planning_run(conn, project_id)
        await_planning_human(conn, project_id)
        return PipelineOutcome.terminal("human-gate")

    # Parse error
    if result.exit_status == "parse_error":
        return PipelineOutcome.retry("parse_error")

    # Failure or timeout
    if result.exit_status in ("failure", "timeout"):
        return PipelineOutcome.retry(result.exit_status)

    # Success requires structured result
    if result.exit_status != "success":
        return PipelineOutcome.retry(result.exit_status)

    review_result = result.structured_result
    if review_result is None or not isinstance(review_result, PlanningReviewResult):
        return PipelineOutcome.retry("parse_error")

    # Validate the review result
    tickets = load_planned_tickets(conn, epic_id)
    valid_sequences = [t["sequence"] for t in tickets]
    validation = validate_planning_review_result(
        review_result.to_dict(), valid_sequences
    )
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

    # Defense-in-depth: verdict/finding consistency
    verdict = review_result.verdict
    has_blocking = any(f.severity == "blocking" for f in review_result.findings)
    if verdict == "fail" and not has_blocking:
        return PipelineOutcome.retry("parse_error")
    if verdict == "pass" and has_blocking:
        return PipelineOutcome.retry("parse_error")

    # Reconcile findings
    is_first_cycle = _is_first_planning_cycle(conn, epic_id)
    reconcile_planning_findings(
        conn=conn,
        epic_id=epic_id,
        review_run_id=run_id,
        new_findings=review_result.findings,
        verdict=verdict,
        is_first_cycle=is_first_cycle,
    )

    # Verdict-based transitions
    if verdict == "fail":
        # Cycle-limit check
        if check_planning_cycle_limit(conn, epic_id, config.limits.max_cycles):
            transition_planned_epic(
                conn,
                epic_id,
                "human-gate",
                "system",
                reason="Cycle limit reached after planning review failure.",
                gate_reason="cycle_limit",
                log_path=log_path,
            )
            finish_planning_run(conn, project_id)
            await_planning_human(conn, project_id)
            return PipelineOutcome.terminal("human-gate")

        transition_planned_epic(
            conn,
            epic_id,
            "revise",
            "system",
            reason="Planning reviewer found blocking issues.",
            log_path=log_path,
        )
        finish_planning_run(conn, project_id)
        set_planning_idle(conn, project_id)
        return PipelineOutcome.terminal("revise")

    if verdict == "pass":
        confidence = review_result.confidence
        if confidence == "low":
            gate_reason = "low_confidence_pass"
        else:
            gate_reason = "review_passed"

        transition_planned_epic(
            conn,
            epic_id,
            "human-gate",
            "system",
            reason=f"Planning review passed with {confidence} confidence.",
            gate_reason=gate_reason,
            log_path=log_path,
        )
        finish_planning_run(conn, project_id)
        await_planning_human(conn, project_id)
        return PipelineOutcome.terminal("human-gate")

    if verdict == "escalate":
        transition_planned_epic(
            conn,
            epic_id,
            "human-gate",
            "system",
            reason="Planning reviewer escalated to human.",
            gate_reason="reviewer_escalated",
            log_path=log_path,
        )
        finish_planning_run(conn, project_id)
        await_planning_human(conn, project_id)
        return PipelineOutcome.terminal("human-gate")

    # Should not be reachable with valid verdicts
    return PipelineOutcome.retry("parse_error")


def _is_first_planning_cycle(conn: sqlite3.Connection, epic_id: str) -> bool:
    """Check whether this is the first review cycle for the epic."""
    row = conn.execute(
        "SELECT current_cycle FROM planned_epics WHERE id = ?", (epic_id,)
    ).fetchone()
    return row["current_cycle"] == 1
