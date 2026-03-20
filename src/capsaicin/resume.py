"""Resume pipeline for ``capsaicin resume`` (T26).

Recovers from interrupted execution based on orchestrator state.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import ReviewResult, RunResult
from capsaicin.config import Config
from capsaicin.orchestrator import (
    check_impl_retry_limit,
    check_review_retry_limit,
    finish_run,
    get_state,
    increment_impl_attempt,
    increment_review_attempt,
    set_idle,
)
from capsaicin.queries import get_impl_run_id, now_utc
from capsaicin.state_machine import transition_ticket
from capsaicin.ticket_review import (
    _handle_review_result,
    _invoke_with_retries as _review_invoke_with_retries,
)
from capsaicin.ticket_run import (
    _handle_run_result,
    _invoke_with_retries as _impl_invoke_with_retries,
    run_implementation_pipeline,
    select_ticket,
)


def get_active_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    """Load an agent run record by ID."""
    row = conn.execute(
        "SELECT id, ticket_id, role, mode, exit_status, verdict, "
        "duration_seconds, raw_stdout, raw_stderr, structured_result, "
        "adapter_metadata, started_at, finished_at "
        "FROM agent_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_active_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict | None:
    """Load a ticket by ID."""
    row = conn.execute(
        "SELECT id, project_id, title, description, status, gate_reason, "
        "blocked_reason, current_cycle, current_impl_attempt, "
        "current_review_attempt "
        "FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _reconstruct_run_result(run: dict) -> RunResult:
    """Reconstruct a RunResult from a DB agent_runs record."""
    structured = None
    if run["structured_result"]:
        structured = ReviewResult.from_json(run["structured_result"])

    return RunResult(
        run_id=run["id"],
        exit_status=run["exit_status"],
        duration_seconds=run["duration_seconds"] or 0.0,
        raw_stdout=run["raw_stdout"] or "",
        raw_stderr=run["raw_stderr"] or "",
        structured_result=structured,
        adapter_metadata=json.loads(run["adapter_metadata"] or "{}"),
    )


def _mark_run_failed(conn: sqlite3.Connection, run_id: str) -> None:
    """Mark an interrupted run as failed."""
    conn.execute(
        "UPDATE agent_runs SET exit_status = 'failure', finished_at = ? WHERE id = ?",
        (now_utc(), run_id),
    )
    conn.commit()


def _handle_interrupted_run(
    conn: sqlite3.Connection,
    project_id: str,
    run: dict,
    config: Config,
    impl_adapter: BaseAdapter,
    review_adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> str:
    """Handle a run that was interrupted (running, no finished_at).

    Marks the run as failed, increments the retry counter, and either
    retries by invoking the adapter again or blocks at the retry limit.
    Returns the final ticket status.
    """
    ticket_id = run["ticket_id"]
    role = run["role"]
    run_id = run["id"]

    _mark_run_failed(conn, run_id)
    finish_run(conn, project_id)

    if log_path:
        log_event(
            log_path,
            "RUN_INTERRUPTED",
            project_id=project_id,
            ticket_id=ticket_id,
            run_id=run_id,
            payload={"role": role, "action": "marked_failure"},
        )

    if role == "implementer":
        increment_impl_attempt(conn, ticket_id)
        if check_impl_retry_limit(conn, ticket_id, config.limits.max_impl_retries):
            transition_ticket(
                conn,
                ticket_id,
                "blocked",
                "system",
                reason="Implementation retry limit exceeded after interrupted run.",
                blocked_reason="implementation_failure",
                log_path=log_path,
            )
            set_idle(conn, project_id)
            return "blocked"

        # Retry: re-invoke the implementation adapter
        ticket_row = conn.execute(
            "SELECT current_cycle FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        return _impl_invoke_with_retries(
            conn=conn,
            project_id=project_id,
            ticket_id=ticket_id,
            cycle_number=ticket_row["current_cycle"],
            attempt_number=0,  # reloaded inside _invoke_with_retries
            config=config,
            adapter=impl_adapter,
            log_path=log_path,
        )
    elif role == "reviewer":
        increment_review_attempt(conn, ticket_id)
        if check_review_retry_limit(conn, ticket_id, config.limits.max_review_retries):
            transition_ticket(
                conn,
                ticket_id,
                "blocked",
                "system",
                reason="Review retry limit exceeded after interrupted run.",
                blocked_reason="reviewer_failure",
                log_path=log_path,
            )
            set_idle(conn, project_id)
            return "blocked"

        # Retry: re-invoke the reviewer adapter
        impl_run_id = get_impl_run_id(conn, ticket_id)
        return _review_invoke_with_retries(
            conn=conn,
            project_id=project_id,
            ticket_id=ticket_id,
            impl_run_id=impl_run_id,
            config=config,
            adapter=review_adapter,
            log_path=log_path,
        )
    else:
        # Unknown role — just clean up
        set_idle(conn, project_id)
        return "idle"


def _handle_finished_impl_run(
    conn: sqlite3.Connection,
    project_id: str,
    run: dict,
    config: Config,
    log_path: str | Path | None = None,
) -> str:
    """Handle a finished-but-unprocessed implementation run.

    Returns the final ticket status.
    """
    ticket_id = run["ticket_id"]
    ticket = get_active_ticket(conn, ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")

    # If ticket has already moved past implementing, work was already processed
    if ticket["status"] != "implementing":
        finish_run(conn, project_id)
        set_idle(conn, project_id)
        return ticket["status"]

    result_status = _handle_run_result(
        conn=conn,
        project_id=project_id,
        ticket_id=ticket_id,
        run_id=run["id"],
        exit_status=run["exit_status"],
        config=config,
        log_path=log_path,
    )

    # _handle_run_result returns '_retry' for failures — in resume context
    # we treat this as needing a fresh run (set idle so user can re-run)
    if result_status == "_retry":
        increment_impl_attempt(conn, ticket_id)
        if check_impl_retry_limit(conn, ticket_id, config.limits.max_impl_retries):
            transition_ticket(
                conn,
                ticket_id,
                "blocked",
                "system",
                reason="Implementation retry limit exceeded on resume.",
                blocked_reason="implementation_failure",
                log_path=log_path,
            )
            finish_run(conn, project_id)
            set_idle(conn, project_id)
            return "blocked"
        finish_run(conn, project_id)
        set_idle(conn, project_id)
        return "implementing"

    return result_status


def _handle_finished_review_run(
    conn: sqlite3.Connection,
    project_id: str,
    run: dict,
    config: Config,
    log_path: str | Path | None = None,
) -> str:
    """Handle a finished-but-unprocessed review run.

    Returns the final ticket status.
    """
    ticket_id = run["ticket_id"]
    ticket = get_active_ticket(conn, ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket '{ticket_id}' not found.")

    # If ticket has already moved past in-review, work was already processed
    if ticket["status"] != "in-review":
        finish_run(conn, project_id)
        set_idle(conn, project_id)
        return ticket["status"]

    impl_run_id = get_impl_run_id(conn, ticket_id)
    result = _reconstruct_run_result(run)

    result_status = _handle_review_result(
        conn=conn,
        project_id=project_id,
        ticket_id=ticket_id,
        impl_run_id=impl_run_id,
        run_id=run["id"],
        result=result,
        config=config,
        log_path=log_path,
    )

    # _handle_review_result returns '_retry:<reason>' for failures — in resume
    # context we treat this as needing a fresh review (set idle so user can re-run)
    if result_status.startswith("_retry"):
        retry_reason = (
            result_status.split(":", 1)[1] if ":" in result_status else "unknown"
        )
        increment_review_attempt(conn, ticket_id)
        if check_review_retry_limit(conn, ticket_id, config.limits.max_review_retries):
            from capsaicin.ticket_review import _retry_reason_to_blocked_reason

            blocked_reason = _retry_reason_to_blocked_reason(retry_reason)
            transition_ticket(
                conn,
                ticket_id,
                "blocked",
                "system",
                reason=f"Review retry limit exceeded on resume ({retry_reason}).",
                blocked_reason=blocked_reason,
                log_path=log_path,
            )
            finish_run(conn, project_id)
            set_idle(conn, project_id)
            return "blocked"
        finish_run(conn, project_id)
        set_idle(conn, project_id)
        return "in-review"

    return result_status


def build_human_gate_context(conn: sqlite3.Connection, ticket_id: str) -> str:
    """Build human-gate context display for awaiting_human state."""
    ticket = get_active_ticket(conn, ticket_id)
    if ticket is None:
        return f"Ticket '{ticket_id}' not found."

    lines = [
        "Awaiting human decision.",
        "",
        f"Ticket: {ticket['id']}",
        f"  Title: {ticket['title']}",
        f"  Status: {ticket['status']}",
        f"  Gate Reason: {ticket['gate_reason'] or 'unknown'}",
    ]

    # Acceptance criteria
    criteria = conn.execute(
        "SELECT description, status FROM acceptance_criteria WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchall()
    lines.append("")
    lines.append("Acceptance Criteria:")
    if criteria:
        for c in criteria:
            lines.append(f"  [{c['status']}] {c['description']}")
    else:
        lines.append("  (none)")

    # Open findings
    findings = conn.execute(
        "SELECT severity, category, location, description "
        "FROM findings WHERE ticket_id = ? AND disposition = 'open' "
        "ORDER BY severity",
        (ticket_id,),
    ).fetchall()
    lines.append("")
    lines.append("Open Findings:")
    if findings:
        for f in findings:
            loc = f" ({f['location']})" if f["location"] else ""
            lines.append(
                f"  [{f['severity']}] [{f['category']}]{loc} {f['description']}"
            )
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Available commands:")
    lines.append("  capsaicin ticket approve  — approve and move to pr-ready")
    lines.append("  capsaicin ticket revise   — send back for revision")
    lines.append("  capsaicin ticket defer    — defer or abandon")

    return "\n".join(lines)


def _resume_from_context(
    conn: sqlite3.Connection,
    project_id: str,
    state: dict,
    resume_context: str,
    config: Config,
    impl_adapter: BaseAdapter,
    review_adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> tuple[str, str]:
    """Parse resume_context and continue from the suspended step.

    resume_context is a JSON string with at least a ``step`` field indicating
    where execution was interrupted.  Recognised steps:

    - ``"run"``    — continue with an implementation run for the active ticket
    - ``"review"`` — continue with a review run for the active ticket

    Unrecognised steps reset the orchestrator to idle so the user can
    manually re-drive the pipeline.
    """
    try:
        ctx = json.loads(resume_context)
    except (json.JSONDecodeError, TypeError):
        set_idle(conn, project_id)
        return (
            "suspended",
            f"Could not parse resume_context: {resume_context!r}. Reset to idle.",
        )

    step = ctx.get("step")
    ticket_id = state.get("active_ticket_id") or ctx.get("ticket_id")

    if not ticket_id:
        set_idle(conn, project_id)
        return ("suspended", "No ticket_id in resume context. Reset to idle.")

    ticket = get_active_ticket(conn, ticket_id)
    if ticket is None:
        set_idle(conn, project_id)
        return ("suspended", f"Ticket '{ticket_id}' not found. Reset to idle.")

    # Clear suspended state before continuing
    conn.execute(
        "UPDATE orchestrator_state SET status = 'idle', "
        "suspended_at = NULL, resume_context = NULL, "
        "active_ticket_id = NULL, active_run_id = NULL, "
        "updated_at = ? WHERE project_id = ?",
        (now_utc(), project_id),
    )
    conn.commit()

    if step == "run":
        if ticket["status"] not in ("ready", "revise"):
            return (
                "suspended",
                f"Resume step 'run' but ticket is in '{ticket['status']}'. "
                f"Reset to idle.",
            )
        final_status = run_implementation_pipeline(
            conn=conn,
            project_id=project_id,
            ticket=ticket,
            config=config,
            adapter=impl_adapter,
            log_path=log_path,
        )
        return ("resumed_run", f"Ticket {ticket_id} -> {final_status}")

    if step == "review":
        from capsaicin.ticket_review import run_review_pipeline

        if ticket["status"] != "in-review":
            return (
                "suspended",
                f"Resume step 'review' but ticket is in '{ticket['status']}'. "
                f"Reset to idle.",
            )
        final_status = run_review_pipeline(
            conn=conn,
            project_id=project_id,
            ticket=ticket,
            config=config,
            adapter=review_adapter,
            allow_drift=ctx.get("allow_drift", False),
            log_path=log_path,
        )
        return ("resumed_review", f"Ticket {ticket_id} -> {final_status}")

    # Unrecognised step
    set_idle(conn, project_id)
    return (
        "suspended",
        f"Unrecognised resume step '{step}'. Reset to idle.",
    )


def resume_pipeline(
    conn: sqlite3.Connection,
    project_id: str,
    config: Config,
    impl_adapter: BaseAdapter,
    review_adapter: BaseAdapter,
    log_path: str | Path | None = None,
) -> tuple[str, str]:
    """Execute the resume pipeline based on orchestrator state.

    Returns a tuple of (action_taken, detail) describing what happened.
    """
    state = get_state(conn, project_id)
    orch_status = state["status"]

    if log_path:
        log_event(
            log_path,
            "RESUME",
            project_id=project_id,
            payload={"orchestrator_status": orch_status},
        )

    # --- idle: behave like ticket run ---
    if orch_status == "idle":
        try:
            ticket = select_ticket(conn)
        except ValueError as e:
            return ("idle", str(e))

        final_status = run_implementation_pipeline(
            conn=conn,
            project_id=project_id,
            ticket=ticket,
            config=config,
            adapter=impl_adapter,
            log_path=log_path,
        )
        return ("run", f"Ticket {ticket['id']} -> {final_status}")

    # --- running: check active run ---
    if orch_status == "running":
        run_id = state["active_run_id"]
        ticket_id = state["active_ticket_id"]

        if run_id is None or ticket_id is None:
            # Orphaned running state — reset to idle
            set_idle(conn, project_id)
            return ("reset", "Orphaned running state reset to idle.")

        run = get_active_run(conn, run_id)
        if run is None:
            set_idle(conn, project_id)
            return ("reset", f"Active run '{run_id}' not found. Reset to idle.")

        if run["finished_at"] is not None:
            # Finished but unprocessed
            if run["role"] == "implementer":
                final_status = _handle_finished_impl_run(
                    conn, project_id, run, config, log_path
                )
                return (
                    "post_run",
                    f"Completed post-run pipeline for implementer run "
                    f"{run_id}. Ticket -> {final_status}",
                )
            elif run["role"] == "reviewer":
                final_status = _handle_finished_review_run(
                    conn, project_id, run, config, log_path
                )
                return (
                    "post_run",
                    f"Completed post-run pipeline for reviewer run "
                    f"{run_id}. Ticket -> {final_status}",
                )
            else:
                # Unknown role — just clean up
                finish_run(conn, project_id)
                set_idle(conn, project_id)
                return ("reset", f"Unknown role '{run['role']}'. Reset to idle.")
        else:
            # Interrupted (no finished_at)
            result_status = _handle_interrupted_run(
                conn,
                project_id,
                run,
                config,
                impl_adapter,
                review_adapter,
                log_path,
            )
            return (
                "interrupted",
                f"Interrupted {run['role']} run {run_id} marked as failure. "
                f"Ticket -> {result_status}",
            )

    # --- awaiting_human: render context ---
    if orch_status == "awaiting_human":
        ticket_id = state["active_ticket_id"]
        if ticket_id is None:
            set_idle(conn, project_id)
            return ("reset", "Awaiting human but no active ticket. Reset to idle.")
        context = build_human_gate_context(conn, ticket_id)
        return ("awaiting_human", context)

    # --- suspended: use resume_context to continue ---
    if orch_status == "suspended":
        resume_context = state.get("resume_context")
        if not resume_context:
            set_idle(conn, project_id)
            return (
                "suspended",
                "Orchestrator was suspended with no resume context. Reset to idle.",
            )

        return _resume_from_context(
            conn=conn,
            project_id=project_id,
            state=state,
            resume_context=resume_context,
            config=config,
            impl_adapter=impl_adapter,
            review_adapter=review_adapter,
            log_path=log_path,
        )

    return ("unknown", f"Unknown orchestrator status: {orch_status}")
