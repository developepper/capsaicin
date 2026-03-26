"""Diff capture module (T14).

Capture tracked-file diffs via ``git diff HEAD`` and persist them in
the ``run_diffs`` table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DiffResult:
    """Result of a diff capture."""

    diff_text: str
    files_changed: list[str]

    @property
    def is_empty(self) -> bool:
        return len(self.diff_text.strip()) == 0


def capture_diff(repo_path: str | Path) -> DiffResult:
    """Run ``git diff HEAD`` in *repo_path* and return a DiffResult.

    Only tracked files are included (no untracked file handling).
    """
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff HEAD failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    diff_text = result.stdout

    # Extract changed file paths from diff --name-only
    name_result = subprocess.run(
        ["git", "diff", "HEAD", "--name-only"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    files_changed = [f for f in name_result.stdout.strip().split("\n") if f]

    return DiffResult(diff_text=diff_text, files_changed=files_changed)


def persist_run_diff(
    conn: sqlite3.Connection, run_id: str, diff_result: DiffResult
) -> None:
    """Insert a DiffResult into ``run_diffs`` for the given run_id."""
    conn.execute(
        "INSERT INTO run_diffs (run_id, diff_text, files_changed) VALUES (?, ?, ?)",
        (run_id, diff_result.diff_text, json.dumps(diff_result.files_changed)),
    )
    conn.commit()


def get_run_diff(conn: sqlite3.Connection, run_id: str) -> DiffResult:
    """Retrieve the persisted DiffResult for *run_id*.

    Raises ``KeyError`` if no diff exists for the given run.
    """
    row = conn.execute(
        "SELECT diff_text, files_changed FROM run_diffs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"No diff found for run_id={run_id!r}")
    return DiffResult(
        diff_text=row["diff_text"],
        files_changed=json.loads(row["files_changed"]),
    )


@dataclass(frozen=True)
class GitMetadata:
    """Snapshot of branch name and commit SHA at a point in time."""

    branch_name: str
    commit_ref: str


def capture_git_metadata(repo_path: str | Path) -> GitMetadata:
    """Return the current branch name and HEAD commit SHA for *repo_path*."""
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    branch_name = (
        branch_result.stdout.strip() if branch_result.returncode == 0 else "HEAD"
    )

    commit_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if commit_result.returncode != 0:
        raise RuntimeError(
            f"Failed to capture git commit ref in {repo_path}: "
            f"{commit_result.stderr.strip()}"
        )
    commit_ref = commit_result.stdout.strip()

    return GitMetadata(branch_name=branch_name, commit_ref=commit_ref)


def diffs_match(a: str, b: str) -> bool:
    """Return True if two diff texts are identical.

    Uses exact string comparison because ``git diff HEAD`` output is the
    review source of truth for workspace-drift detection.  Any difference
    — including trailing whitespace in file content — constitutes real
    drift that downstream commands (T16/T20) must not silently ignore.
    """
    return a == b
