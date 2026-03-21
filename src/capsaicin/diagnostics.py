"""Run-outcome diagnostics for operator-facing messaging (T02).

Shared helpers that build human-readable explanations from persisted run
data.  Used by ``ticket run`` CLI output, ``loop`` stop messages, and
``resume.build_human_gate_context``.
"""

from __future__ import annotations

import json
import sqlite3

# Maximum characters of agent result text to surface in a diagnostic message.
_MAX_RESULT_TEXT_LEN = 300


def _extract_result_text_from_raw(raw_stdout: str | None) -> str:
    """Extract the ``result`` field from a persisted Claude envelope.

    Returns the assistant text, or an empty string if extraction fails.
    """
    if not raw_stdout:
        return ""
    try:
        envelope = json.loads(raw_stdout)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(envelope, dict):
        return ""
    result = envelope.get("result")
    return result if isinstance(result, str) else ""


def _truncate(text: str, max_len: int = _MAX_RESULT_TEXT_LEN) -> str:
    """Truncate *text* to *max_len* characters, appending '…' if trimmed."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _denial_summary(adapter_metadata: dict) -> str:
    """Build a concise denial summary from adapter_metadata.

    Returns a string like ``"3 denied actions (Edit, Bash)"`` or empty
    string if no denial data is present.
    """
    normalized = adapter_metadata.get("normalized_denials", [])
    if not normalized:
        # Fall back to raw permission_denials for counting
        raw = adapter_metadata.get("permission_denials", [])
        if not raw:
            return ""
        return f"{len(raw)} denied action(s)"

    count = len(normalized)
    tool_names = sorted({d.get("tool_name", "unknown") for d in normalized})
    tools_str = ", ".join(tool_names)
    return f"{count} denied action(s) ({tools_str})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_run_outcome_message(
    conn: sqlite3.Connection,
    ticket_id: str,
    run_id: str | None = None,
) -> str:
    """Build a diagnostic message for the most recent run on a ticket.

    If *run_id* is given, use that run; otherwise use the latest run for
    the ticket.  Returns a concise explanation suitable for CLI output.

    Distinguishes:
    - empty implementation (no changes made)
    - blocked by permissions (denied actions + remediation text)
    - other outcomes (plain status line)
    """
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
        return ""

    exit_status = row["exit_status"]
    raw_stdout = row["raw_stdout"] or ""
    meta_json = row["adapter_metadata"] or "{}"
    try:
        meta = json.loads(meta_json)
    except (json.JSONDecodeError, TypeError):
        meta = {}

    result_text = _extract_result_text_from_raw(raw_stdout)

    # --- Permission denied ---
    if exit_status == "permission_denied":
        lines = ["Run blocked by permission denials."]
        summary = _denial_summary(meta)
        if summary:
            lines.append(f"  {summary}")
        if result_text:
            lines.append("")
            lines.append(f"  Agent: {_truncate(result_text)}")
        return "\n".join(lines)

    # --- Empty implementation (success with gate_reason='empty_implementation') ---
    # We detect this by checking the ticket's gate_reason, since exit_status is
    # "success" for both empty and non-empty implementations.
    gate_row = conn.execute(
        "SELECT gate_reason FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    gate_reason = gate_row["gate_reason"] if gate_row else None

    if exit_status == "success" and gate_reason == "empty_implementation":
        lines = ["Implementation produced no changes."]
        if result_text:
            lines.append("")
            lines.append(f"  Agent: {_truncate(result_text)}")
        return "\n".join(lines)

    # --- Other outcomes: just return a plain status line ---
    return ""


def build_human_gate_diagnostic(
    conn: sqlite3.Connection,
    ticket_id: str,
) -> str:
    """Build diagnostic text for a ticket in human-gate.

    Returns the run-outcome message for the most recent run, or empty
    string if not applicable.
    """
    return build_run_outcome_message(conn, ticket_id)
