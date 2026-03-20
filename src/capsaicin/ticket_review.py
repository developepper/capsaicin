"""Review pipeline for ``capsaicin ticket review`` (T20).

All pipeline logic lives in reusable functions so that T26 (resume) and
T27 (loop) can call them without going through the CLI.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import AcceptanceCriterion, Finding, RunRequest
from capsaicin.config import Config
from capsaicin.criteria import update_criteria_from_review
from capsaicin.diff import get_run_diff
from capsaicin.orchestrator import (
    await_human,
    check_cycle_limit,
    check_review_retry_limit,
    finish_run,
    increment_review_attempt,
    set_idle,
    start_run,
)
from capsaicin.prompts import build_reviewer_prompt
from capsaicin.reconciliation import reconcile_findings
from capsaicin.review_baseline import (
    WorkspaceDriftError,
    capture_review_baseline,
    check_review_violation,
    handle_drift,
)
from capsaicin.state_machine import transition_ticket


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id() -> str:
    from ulid import ULID

    return str(ULID())


# ---------------------------------------------------------------------------
# Ticket selection
# ---------------------------------------------------------------------------


def select_review_ticket(
    conn: sqlite3.Connection, ticket_id: str | None = None
) -> dict:
    """Select a ticket for review.

    If *ticket_id* is given, validate that it exists and is in ``in-review``
    status.  Otherwise auto-select the first ``in-review`` ticket ordered
    by ``status_changed_at``.

    Returns a dict with ticket row data.
    Raises ``ValueError`` if no eligible ticket is found.
    """
    if ticket_id:
        row = conn.execute(
            "SELECT id, project_id, title, description, status, "
            "current_cycle, current_impl_attempt, current_review_attempt "
            "FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Ticket '{ticket_id}' not found.")
        if row["status"] != "in-review":
            raise ValueError(
                f"Ticket '{ticket_id}' is in '{row['status']}' status; "
                "expected 'in-review'."
            )
        return dict(row)

    # Auto-select: first in-review ticket by status_changed_at
    row = conn.execute(
        "SELECT id, project_id, title, description, status, "
        "current_cycle, current_impl_attempt, current_review_attempt "
        "FROM tickets WHERE status = 'in-review' "
        "ORDER BY status_changed_at"
    ).fetchone()

    if row is None:
        raise ValueError("No ticket found in 'in-review' status.")

    return dict(row)


# ---------------------------------------------------------------------------
# Run record helpers
# ---------------------------------------------------------------------------


def _insert_reviewer_run(
    conn: sqlite3.Connection,
    run_id: str,
    ticket_id: str,
    cycle_number: int,
    attempt_number: int,
    prompt: str,
    run_request_json: str,
    diff_context: str,
) -> None:
    """Insert an agent_runs row with exit_status='running' for a reviewer."""
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, diff_context, started_at) "
        "VALUES (?, ?, 'reviewer', 'read-only', ?, ?, 'running', ?, ?, ?, ?)",
        (
            run_id,
            ticket_id,
            cycle_number,
            attempt_number,
            prompt,
            run_request_json,
            diff_context,
            _now(),
        ),
    )
    conn.commit()


def _update_reviewer_run(
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
            _now(),
            run_id,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Context loaders
# ---------------------------------------------------------------------------


def _load_criteria(
    conn: sqlite3.Connection, ticket_id: str
) -> list[AcceptanceCriterion]:
    rows = conn.execute(
        "SELECT id, description, status FROM acceptance_criteria WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchall()
    return [
        AcceptanceCriterion(
            id=r["id"], description=r["description"], status=r["status"]
        )
        for r in rows
    ]


def _load_open_findings(conn: sqlite3.Connection, ticket_id: str) -> list[Finding]:
    rows = conn.execute(
        "SELECT severity, category, description, location, "
        "acceptance_criterion_id, disposition "
        "FROM findings WHERE ticket_id = ? AND disposition = 'open'",
        (ticket_id,),
    ).fetchall()
    return [
        Finding(
            severity=r["severity"],
            category=r["category"],
            description=r["description"],
            location=r["location"],
            acceptance_criterion_id=r["acceptance_criterion_id"],
            disposition=r["disposition"],
        )
        for r in rows
    ]


def _get_impl_run_id(conn: sqlite3.Connection, ticket_id: str) -> str:
    """Get the most recent implementer run ID for a ticket."""
    row = conn.execute(
        "SELECT id FROM agent_runs "
        "WHERE ticket_id = ? AND role = 'implementer' "
        "ORDER BY started_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No implementer run found for ticket '{ticket_id}'.")
    return row["id"]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_review_pipeline(
    conn: sqlite3.Connection,
    project_id: str,
    ticket: dict,
    config: Config,
    adapter: BaseAdapter,
    allow_drift: bool = False,
    log_path: str | Path | None = None,
) -> str:
    """Execute the full review pipeline for a ticket.

    Returns the final ticket status after the pipeline completes.
    """
    ticket_id = ticket["id"]

    # --- Find the implementation run for drift/baseline checks ---
    impl_run_id = _get_impl_run_id(conn, ticket_id)

    # --- Workspace drift check (T16) ---
    handle_drift(conn, impl_run_id, config.project.repo_path, allow_drift)

    # --- Invoke with retries ---
    return _invoke_with_retries(
        conn=conn,
        project_id=project_id,
        ticket_id=ticket_id,
        impl_run_id=impl_run_id,
        config=config,
        adapter=adapter,
        log_path=log_path,
    )


def _invoke_with_retries(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    impl_run_id: str,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> str:
    """Invoke the reviewer adapter, handling retries on errors.

    Returns the final ticket status.
    """
    while True:
        # Reload attempt number
        ticket_row = conn.execute(
            "SELECT current_review_attempt FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        attempt_number = ticket_row["current_review_attempt"]

        result_status = _invoke_once(
            conn=conn,
            project_id=project_id,
            ticket_id=ticket_id,
            impl_run_id=impl_run_id,
            attempt_number=attempt_number,
            config=config,
            adapter=adapter,
            log_path=log_path,
        )

        if result_status != "_retry":
            return result_status

        # Check retry limit before looping
        if check_review_retry_limit(conn, ticket_id, config.limits.max_review_retries):
            transition_ticket(
                conn,
                ticket_id,
                "blocked",
                "system",
                reason="Review retry limit exceeded.",
                blocked_reason="reviewer_contract_violation",
                log_path=log_path,
            )
            finish_run(conn, project_id)
            set_idle(conn, project_id)
            if log_path:
                log_event(
                    log_path,
                    "RETRY_LIMIT",
                    project_id=project_id,
                    ticket_id=ticket_id,
                    payload={"max_retries": config.limits.max_review_retries},
                )
            return "blocked"

        # Increment attempt and loop
        increment_review_attempt(conn, ticket_id)


def _invoke_once(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    impl_run_id: str,
    attempt_number: int,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> str:
    """Single reviewer invocation. Returns ticket status or '_retry'."""
    run_id = _generate_id()

    # Load context
    criteria = _load_criteria(conn, ticket_id)
    prior_findings = _load_open_findings(conn, ticket_id)
    ticket_row = conn.execute(
        "SELECT title, description, current_cycle FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    cycle_number = ticket_row["current_cycle"]

    # Get the implementation diff for review context
    impl_diff = get_run_diff(conn, impl_run_id)

    # Assemble reviewer prompt
    prompt = build_reviewer_prompt(
        ticket={"title": ticket_row["title"], "description": ticket_row["description"]},
        criteria=criteria,
        diff_context=impl_diff.diff_text,
        prior_findings=prior_findings,
    )

    run_request = RunRequest(
        run_id=run_id,
        role="reviewer",
        mode="read-only",
        working_directory=config.project.repo_path,
        prompt=prompt,
        diff_context=impl_diff.diff_text,
        acceptance_criteria=criteria,
        prior_findings=prior_findings,
        timeout_seconds=config.limits.timeout_seconds,
        adapter_config={
            "backend": config.reviewer.backend,
            "command": config.reviewer.command,
            "allowed_tools": config.reviewer.allowed_tools,
        },
    )

    # Insert run record (must precede baseline capture due to FK)
    _insert_reviewer_run(
        conn,
        run_id,
        ticket_id,
        cycle_number,
        attempt_number,
        prompt,
        run_request.to_json(),
        impl_diff.diff_text,
    )

    # Capture review baseline (T16) — immediately before adapter invocation
    capture_review_baseline(conn, config.project.repo_path, run_id)

    # Update orchestrator state
    start_run(conn, project_id, ticket_id, run_id)

    if log_path:
        log_event(
            log_path,
            "RUN_START",
            project_id=project_id,
            ticket_id=ticket_id,
            run_id=run_id,
            payload={
                "role": "reviewer",
                "cycle": cycle_number,
                "attempt": attempt_number,
            },
        )

    # Invoke adapter
    result = adapter.execute(run_request)

    # Update run record with basic result info
    structured_json = (
        result.structured_result.to_json() if result.structured_result else None
    )
    _update_reviewer_run(
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
            ticket_id=ticket_id,
            run_id=run_id,
            payload={
                "exit_status": result.exit_status,
                "duration": result.duration_seconds,
            },
        )

    # --- Post-review checks and result handling ---
    return _handle_review_result(
        conn=conn,
        project_id=project_id,
        ticket_id=ticket_id,
        impl_run_id=impl_run_id,
        run_id=run_id,
        result=result,
        config=config,
        log_path=log_path,
    )


def _handle_review_result(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    impl_run_id: str,
    run_id: str,
    result,
    config: Config,
    log_path: str | Path | None = None,
) -> str:
    """Process the reviewer result and transition the ticket.

    Returns the new ticket status, or '_retry' if the caller should retry.
    """
    # --- Contract violation check (T16) ---
    if result.exit_status == "success":
        violation = check_review_violation(conn, config.project.repo_path, run_id)
        if violation:
            # Reviewer modified tracked files — contract violation
            _update_reviewer_run(
                conn,
                run_id,
                exit_status="contract_violation",
                duration_seconds=result.duration_seconds,
                raw_stdout=result.raw_stdout,
                raw_stderr=result.raw_stderr,
                adapter_metadata=result.adapter_metadata,
            )
            if log_path:
                log_event(
                    log_path,
                    "CONTRACT_VIOLATION",
                    project_id=project_id,
                    ticket_id=ticket_id,
                    run_id=run_id,
                    payload={"reason": "reviewer modified tracked files"},
                )
            return "_retry"

    # --- Parse error (adapter already returned parse_error) ---
    if result.exit_status == "parse_error":
        if log_path:
            log_event(
                log_path,
                "PARSE_ERROR",
                project_id=project_id,
                ticket_id=ticket_id,
                run_id=run_id,
                payload={"reason": "review result validation failed"},
            )
        return "_retry"

    # --- Failure or timeout ---
    if result.exit_status in ("failure", "timeout"):
        return "_retry"

    # --- Success with valid structured result ---
    review_result = result.structured_result
    if review_result is None:
        # Should not happen if adapter returned success, but be defensive
        return "_retry"

    verdict = review_result.verdict
    confidence = review_result.confidence
    is_first_cycle = _is_first_cycle(conn, ticket_id)

    # --- Reconcile findings (T19) ---
    reconcile_findings(
        conn=conn,
        ticket_id=ticket_id,
        review_run_id=run_id,
        impl_run_id=impl_run_id,
        new_findings=review_result.findings,
        verdict=verdict,
        is_first_cycle=is_first_cycle,
    )

    # --- Update acceptance criteria (T18) ---
    update_criteria_from_review(conn, ticket_id, review_result)

    # --- Verdict-based transitions ---
    if verdict == "fail":
        # Cycle-limit check: if at the limit, go to human-gate instead of revise
        if check_cycle_limit(conn, ticket_id, config.limits.max_cycles):
            transition_ticket(
                conn,
                ticket_id,
                "human-gate",
                "system",
                reason="Cycle limit reached after review failure.",
                gate_reason="cycle_limit",
                log_path=log_path,
            )
            finish_run(conn, project_id)
            await_human(conn, project_id)
            if log_path:
                log_event(
                    log_path,
                    "CYCLE_LIMIT",
                    project_id=project_id,
                    ticket_id=ticket_id,
                    payload={"max_cycles": config.limits.max_cycles},
                )
            return "human-gate"

        transition_ticket(
            conn,
            ticket_id,
            "revise",
            "system",
            reason="Reviewer found blocking issues.",
            log_path=log_path,
        )
        finish_run(conn, project_id)
        set_idle(conn, project_id)
        return "revise"

    if verdict == "pass":
        if confidence == "low":
            gate_reason = "low_confidence_pass"
        else:
            gate_reason = "review_passed"

        transition_ticket(
            conn,
            ticket_id,
            "human-gate",
            "system",
            reason=f"Review passed with {confidence} confidence.",
            gate_reason=gate_reason,
            log_path=log_path,
        )
        finish_run(conn, project_id)
        await_human(conn, project_id)
        return "human-gate"

    if verdict == "escalate":
        transition_ticket(
            conn,
            ticket_id,
            "human-gate",
            "system",
            reason="Reviewer escalated to human.",
            gate_reason="reviewer_escalated",
            log_path=log_path,
        )
        finish_run(conn, project_id)
        await_human(conn, project_id)
        return "human-gate"

    # Should not be reachable with valid verdicts, but be safe
    return "_retry"


def _is_first_cycle(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """Check whether this is the first review cycle for the ticket."""
    row = conn.execute(
        "SELECT current_cycle FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    return row["current_cycle"] == 1
