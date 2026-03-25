"""Implementation pipeline for ``capsaicin ticket run`` (T15).

All pipeline logic lives in reusable functions so that T26 (resume) and
T27 (loop) can call them without going through the CLI.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from capsaicin.activity_log import build_run_end_payload, log_event
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import RunRequest
from capsaicin.config import Config
from capsaicin.resolver import resolve_adapter_config
from capsaicin.diff import capture_diff, persist_run_diff
from capsaicin.pipeline_outcome import PipelineOutcome
from capsaicin.orchestrator import (
    await_human,
    check_cycle_limit,
    check_impl_retry_limit,
    finish_run,
    increment_cycle,
    increment_impl_attempt,
    init_cycle,
    set_idle,
    start_run,
)
from capsaicin.prompts import build_implementer_prompt
from capsaicin.errors import InvalidStatusError, NoEligibleTicketError
from capsaicin.queries import (
    TICKET_COLUMNS,
    check_evidence_completeness,
    generate_id,
    load_backend_evidence_for_epic,
    load_criteria,
    load_open_findings,
    load_ticket,
    now_utc,
    record_run_evidence,
)
from capsaicin.state_machine import transition_ticket


# ---------------------------------------------------------------------------
# Ticket selection
# ---------------------------------------------------------------------------


def select_ticket(conn: sqlite3.Connection, ticket_id: str | None = None) -> dict:
    """Select a ticket for implementation.

    If *ticket_id* is given, validate that it exists and is in ``ready``
    or ``revise`` status.  Otherwise auto-select the next ``ready`` ticket
    whose dependencies are all ``done``, ordered by ``created_at``.

    Returns a dict with ticket row data.
    Raises ``ValueError`` if no eligible ticket is found.
    """
    if ticket_id:
        ticket = load_ticket(conn, ticket_id)
        if ticket["status"] not in ("ready", "revise"):
            raise InvalidStatusError(ticket_id, ticket["status"], ("ready", "revise"))
        return ticket

    # Auto-select: next ready ticket with all deps done, ordered by created_at
    rows = conn.execute(
        f"SELECT {TICKET_COLUMNS} "
        "FROM tickets WHERE status = 'ready' ORDER BY created_at"
    ).fetchall()

    for row in rows:
        # Check all dependencies are done
        deps = conn.execute(
            "SELECT t.status FROM ticket_dependencies td "
            "JOIN tickets t ON t.id = td.depends_on_id "
            "WHERE td.ticket_id = ?",
            (row["id"],),
        ).fetchall()
        if all(d["status"] == "done" for d in deps):
            return dict(row)

    raise NoEligibleTicketError(
        "No eligible ticket found (no 'ready' tickets with satisfied dependencies)."
    )


# ---------------------------------------------------------------------------
# Run record helpers
# ---------------------------------------------------------------------------


def _insert_impl_run(
    conn: sqlite3.Connection,
    run_id: str,
    ticket_id: str,
    cycle_number: int,
    attempt_number: int,
    prompt: str,
    run_request_json: str,
) -> None:
    """Insert an agent_runs row with exit_status='running'."""
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'implementer', 'read-write', ?, ?, 'running', ?, ?, ?)",
        (
            run_id,
            ticket_id,
            cycle_number,
            attempt_number,
            prompt,
            run_request_json,
            now_utc(),
        ),
    )
    conn.commit()


def _update_impl_run(
    conn: sqlite3.Connection,
    run_id: str,
    exit_status: str,
    duration_seconds: float,
    raw_stdout: str,
    raw_stderr: str,
    adapter_metadata: dict | None,
) -> None:
    """Update an agent_runs row with terminal status and outputs."""
    conn.execute(
        "UPDATE agent_runs SET "
        "exit_status = ?, duration_seconds = ?, "
        "raw_stdout = ?, raw_stderr = ?, "
        "adapter_metadata = ?, structured_result = NULL, "
        "finished_at = ? "
        "WHERE id = ?",
        (
            exit_status,
            duration_seconds,
            raw_stdout,
            raw_stderr,
            json.dumps(adapter_metadata or {}),
            now_utc(),
            run_id,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_implementation_pipeline(
    conn: sqlite3.Connection,
    project_id: str,
    ticket: dict,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
    epic_id: str | None = None,
) -> str:
    """Execute the full implementation pipeline for a ticket.

    Returns the final ticket status after the pipeline completes.
    """
    ticket_id = ticket["id"]
    from_status = ticket["status"]

    # --- Cycle-limit shortcut (revise only) ---
    if from_status == "revise":
        if check_cycle_limit(conn, ticket_id, config.limits.max_cycles):
            transition_ticket(
                conn,
                ticket_id,
                "human-gate",
                "system",
                reason="Cycle limit reached before re-implementation.",
                gate_reason="cycle_limit",
                log_path=log_path,
            )
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

    # --- Transition to implementing ---
    transition_ticket(
        conn,
        ticket_id,
        "implementing",
        "system",
        reason="Starting implementation run.",
        log_path=log_path,
    )

    # --- Cycle management ---
    if from_status == "ready":
        init_cycle(conn, ticket_id)
    elif from_status == "revise":
        increment_cycle(conn, ticket_id)

    # Reload ticket to get updated counters
    ticket_row = conn.execute(
        "SELECT current_cycle, current_impl_attempt FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    cycle_number = ticket_row["current_cycle"]
    attempt_number = ticket_row["current_impl_attempt"]

    # --- Invoke adapter (with retry loop) ---
    return invoke_impl_with_retries(
        conn=conn,
        project_id=project_id,
        ticket_id=ticket_id,
        cycle_number=cycle_number,
        attempt_number=attempt_number,
        config=config,
        adapter=adapter,
        log_path=log_path,
        epic_id=epic_id,
    )


def invoke_impl_with_retries(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    cycle_number: int,
    attempt_number: int,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
    epic_id: str | None = None,
) -> str:
    """Invoke the adapter, handling retries on failure/timeout.

    Returns the final ticket status.
    """
    while True:
        # Reload attempt number (may have been incremented by retry)
        ticket_row = conn.execute(
            "SELECT current_impl_attempt FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        attempt_number = ticket_row["current_impl_attempt"]

        outcome = _impl_invoke_once(
            conn=conn,
            project_id=project_id,
            ticket_id=ticket_id,
            cycle_number=cycle_number,
            attempt_number=attempt_number,
            config=config,
            adapter=adapter,
            log_path=log_path,
            epic_id=epic_id,
        )

        if not outcome.should_retry:
            return outcome.status

        # Check retry limit before looping
        if check_impl_retry_limit(conn, ticket_id, config.limits.max_impl_retries):
            transition_ticket(
                conn,
                ticket_id,
                "blocked",
                "system",
                reason="Implementation retry limit exceeded.",
                blocked_reason="implementation_failure",
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
                    payload={"max_retries": config.limits.max_impl_retries},
                )
            return "blocked"

        # Increment attempt and loop
        increment_impl_attempt(conn, ticket_id)


def _impl_invoke_once(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    cycle_number: int,
    attempt_number: int,
    config: Config,
    adapter: BaseAdapter,
    log_path: str | Path | None = None,
    epic_id: str | None = None,
) -> PipelineOutcome:
    """Single adapter invocation. Returns a PipelineOutcome."""
    run_id = generate_id()

    # Load context
    criteria = load_criteria(conn, ticket_id)
    prior_findings = load_open_findings(conn, ticket_id)
    ticket_row = conn.execute(
        "SELECT title, description FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()

    # Load evidence from parent epic when the ticket has lineage
    evidence = None
    pending_evidence_descriptions = None
    if epic_id:
        evidence = load_backend_evidence_for_epic(conn, epic_id) or None
        pending_reqs = check_evidence_completeness(conn, epic_id)
        if pending_reqs:
            pending_evidence_descriptions = [r.description for r in pending_reqs]

    # Assemble prompt and request
    prompt = build_implementer_prompt(
        ticket={"title": ticket_row["title"], "description": ticket_row["description"]},
        criteria=criteria,
        prior_findings=prior_findings,
        cycle=cycle_number,
        max_cycles=config.limits.max_cycles,
        evidence=evidence,
        pending_evidence_descriptions=pending_evidence_descriptions,
    )

    resolved = resolve_adapter_config(
        config,
        role="implementer",
        conn=conn,
        ticket_id=ticket_id,
        epic_id=epic_id,
    )
    run_request = RunRequest(
        run_id=run_id,
        role="implementer",
        mode="read-write",
        working_directory=config.project.repo_path,
        prompt=prompt,
        acceptance_criteria=criteria,
        prior_findings=prior_findings,
        timeout_seconds=config.limits.timeout_seconds,
        adapter_config={
            "backend": resolved.backend,
            "command": resolved.command,
            "model": resolved.model,
        },
    )

    # Insert run record
    _insert_impl_run(
        conn,
        run_id,
        ticket_id,
        cycle_number,
        attempt_number,
        prompt,
        run_request.to_json(),
    )

    # Record which evidence was included in this run's prompt (T09)
    if evidence:
        record_run_evidence(conn, run_id, [e.id for e in evidence])
        conn.commit()

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
                "role": "implementer",
                "cycle": cycle_number,
                "attempt": attempt_number,
            },
        )

    # Invoke adapter
    result = adapter.execute(run_request)

    # Update run record
    _update_impl_run(
        conn,
        run_id,
        exit_status=result.exit_status,
        duration_seconds=result.duration_seconds,
        raw_stdout=result.raw_stdout,
        raw_stderr=result.raw_stderr,
        adapter_metadata=result.adapter_metadata,
    )

    if log_path:
        log_event(
            log_path,
            "RUN_END",
            project_id=project_id,
            ticket_id=ticket_id,
            run_id=run_id,
            payload=build_run_end_payload(
                result.exit_status,
                result.duration_seconds,
                result.adapter_metadata,
            ),
        )

    # Handle result — return PipelineOutcome
    return handle_run_result(
        conn=conn,
        project_id=project_id,
        ticket_id=ticket_id,
        run_id=run_id,
        exit_status=result.exit_status,
        config=config,
        log_path=log_path,
    )


def handle_run_result(
    conn: sqlite3.Connection,
    project_id: str,
    ticket_id: str,
    run_id: str,
    exit_status: str,
    config: Config,
    log_path: str | Path | None = None,
) -> PipelineOutcome:
    """Process the adapter result and transition the ticket.

    Returns a ``PipelineOutcome`` — either a terminal status or a retry signal.
    """
    # Permission denied — route to human-gate without consuming retries
    if exit_status == "permission_denied":
        transition_ticket(
            conn,
            ticket_id,
            "human-gate",
            "system",
            reason="Implementer run blocked by permission denials.",
            gate_reason="permission_denied",
            log_path=log_path,
        )
        finish_run(conn, project_id)
        await_human(conn, project_id)
        if log_path:
            log_event(
                log_path,
                "PERMISSION_DENIED",
                project_id=project_id,
                ticket_id=ticket_id,
                run_id=run_id,
                payload={"role": "implementer"},
            )
        return PipelineOutcome.terminal("human-gate")

    if exit_status == "success":
        # Capture post-run diff
        diff_result = capture_diff(config.project.repo_path)

        if not diff_result.is_empty:
            persist_run_diff(conn, run_id, diff_result)
            transition_ticket(
                conn,
                ticket_id,
                "in-review",
                "system",
                reason="Implementation produced changes.",
                log_path=log_path,
            )
            finish_run(conn, project_id)
            set_idle(conn, project_id)
            return PipelineOutcome.terminal("in-review")
        else:
            # Empty implementation
            transition_ticket(
                conn,
                ticket_id,
                "human-gate",
                "system",
                reason="Implementation produced no changes.",
                gate_reason="empty_implementation",
                log_path=log_path,
            )
            finish_run(conn, project_id)
            await_human(conn, project_id)
            return PipelineOutcome.terminal("human-gate")

    # Failure or timeout — signal retry
    return PipelineOutcome.retry(exit_status)
