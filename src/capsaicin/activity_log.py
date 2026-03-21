"""Append-only activity log helper.

Format: <ISO8601> <EVENT_TYPE> [project_id=X] [ticket_id=X] [run_id=X] <JSON>
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def log_event(
    log_path: str | Path,
    event_type: str,
    *,
    project_id: str | None = None,
    ticket_id: str | None = None,
    run_id: str | None = None,
    payload: dict | None = None,
) -> None:
    """Append a single event line to the activity log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [ts, event_type]
    if project_id:
        parts.append(f"project_id={project_id}")
    if ticket_id:
        parts.append(f"ticket_id={ticket_id}")
    if run_id:
        parts.append(f"run_id={run_id}")
    parts.append(json.dumps(payload or {}, separators=(",", ":")))
    line = " ".join(parts) + "\n"
    with open(log_path, "a") as f:
        f.write(line)


def build_run_end_payload(
    exit_status: str,
    duration_seconds: float,
    adapter_metadata: dict | None = None,
) -> dict:
    """Build a RUN_END log payload with cost and denial details (T03).

    Extends the base payload with:
    - ``total_cost_usd`` when the adapter reports it
    - ``denial_count`` and ``denials`` for permission-denied runs, using the
      same normalized denial shape from T01
    """
    payload: dict = {
        "exit_status": exit_status,
        "duration": duration_seconds,
    }

    if not adapter_metadata:
        return payload

    # Cost
    cost = adapter_metadata.get("total_cost_usd")
    if cost is not None:
        payload["total_cost_usd"] = cost

    # Permission denials — use the same normalized shape from T01
    normalized = adapter_metadata.get("normalized_denials", [])
    if normalized:
        payload["denial_count"] = len(normalized)
        payload["denials"] = _log_denials(normalized)
    else:
        # Fall back to raw permission_denials for count only
        raw = adapter_metadata.get("permission_denials", [])
        if raw and isinstance(raw, list) and any(isinstance(d, dict) for d in raw):
            payload["denial_count"] = len(raw)

    return payload


def _log_denials(normalized: list[dict]) -> list[dict]:
    """Produce denial entries for logging using the T01 normalized shape.

    Each entry preserves ``tool_name`` and, when present, ``file_path``
    (for Edit/Write tools) or ``command`` (for Bash tools).
    ``tool_use_id`` is omitted to keep the log compact.
    """
    compact = []
    for d in normalized:
        entry: dict = {"tool_name": d.get("tool_name", "unknown")}
        if "file_path" in d:
            entry["file_path"] = d["file_path"]
        if "command" in d:
            entry["command"] = d["command"]
        compact.append(entry)
    return compact
