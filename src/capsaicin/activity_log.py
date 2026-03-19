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
