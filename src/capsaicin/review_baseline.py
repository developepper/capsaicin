"""Review baseline and workspace drift check (T16).

Captures pre-review baselines, detects workspace drift before review,
and detects reviewer contract violations (tracked-file modifications)
after review.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from capsaicin.diff import capture_diff, diffs_match, get_run_diff, persist_run_diff


class WorkspaceDriftError(Exception):
    """Raised when workspace drift is detected and allow_drift is False."""


@dataclass
class DriftResult:
    """Result of a workspace drift check."""

    has_drift: bool
    expected_diff: str
    actual_diff: str


def check_workspace_drift(
    conn: sqlite3.Connection, repo_path: str | Path, run_id: str
) -> DriftResult:
    """Compare current ``git diff HEAD`` against persisted ``run_diffs.diff_text``.

    Returns a DriftResult indicating whether the workspace has drifted
    from the diff captured at the end of the implementation run.
    """
    stored = get_run_diff(conn, run_id)
    current = capture_diff(repo_path)
    return DriftResult(
        has_drift=not diffs_match(stored.diff_text, current.diff_text),
        expected_diff=stored.diff_text,
        actual_diff=current.diff_text,
    )


def handle_drift(
    conn: sqlite3.Connection,
    run_id: str,
    repo_path: str | Path,
    allow_drift: bool,
) -> None:
    """Check for workspace drift and handle it.

    If drift is detected and *allow_drift* is True, re-capture the
    current diff as the new ``run_diffs`` baseline.  If drift is
    detected and *allow_drift* is False, raise ``WorkspaceDriftError``.
    If there is no drift, this is a no-op.
    """
    result = check_workspace_drift(conn, repo_path, run_id)
    if not result.has_drift:
        return

    if not allow_drift:
        raise WorkspaceDriftError(
            "Workspace has drifted from the implementation diff. "
            "Use --allow-drift to re-capture the current diff as the new baseline."
        )

    # Re-capture: delete old run_diffs row and insert new one
    conn.execute("DELETE FROM run_diffs WHERE run_id = ?", (run_id,))
    conn.commit()
    new_diff = capture_diff(repo_path)
    persist_run_diff(conn, run_id, new_diff)


def capture_review_baseline(
    conn: sqlite3.Connection, repo_path: str | Path, run_id: str
) -> None:
    """Snapshot the current tracked-file diff state into ``review_baselines``.

    This should be called immediately before the reviewer is invoked so
    that post-review comparison can detect whether the reviewer modified
    tracked files (a contract violation).
    """
    current = capture_diff(repo_path)
    conn.execute(
        "INSERT INTO review_baselines (run_id, baseline_diff, baseline_status) "
        "VALUES (?, ?, ?)",
        (run_id, current.diff_text, "captured"),
    )
    conn.commit()


def check_review_violation(
    conn: sqlite3.Connection, repo_path: str | Path, run_id: str
) -> bool:
    """Compare post-review tracked-file state to the captured baseline.

    Returns True if the reviewer modified tracked files (a contract
    violation).  Also persists the post-review diff and violation flag
    into the ``review_baselines`` row.
    """
    row = conn.execute(
        "SELECT baseline_diff FROM review_baselines WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"No review baseline found for run_id={run_id!r}")

    baseline_diff = row["baseline_diff"]
    current = capture_diff(repo_path)
    violation = not diffs_match(baseline_diff, current.diff_text)

    conn.execute(
        "UPDATE review_baselines SET post_diff = ?, violation = ? WHERE run_id = ?",
        (current.diff_text, 1 if violation else 0, run_id),
    )
    conn.commit()

    return violation
