"""Tests for review baseline and workspace drift check (T16)."""

from __future__ import annotations

import subprocess

import pytest

from capsaicin.db import get_connection, run_migrations
from capsaicin.diff import DiffResult, capture_diff, persist_run_diff, get_run_diff
from capsaicin.review_baseline import (
    DriftResult,
    WorkspaceDriftError,
    capture_review_baseline,
    check_review_violation,
    check_workspace_drift,
    handle_drift,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory database with full schema."""
    conn = get_connection(":memory:")
    run_migrations(conn)
    return conn


@pytest.fixture()
def git_repo(tmp_path):
    """Temporary git repo with one committed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "file.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def _insert_run(db, run_id="run-1", ticket_id="ticket-1"):
    """Insert minimal rows to satisfy FK constraints for run_diffs and review_baselines."""
    db.execute(
        "INSERT OR IGNORE INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
        ("proj-1", "test", "/tmp"),
    )
    db.execute(
        "INSERT OR IGNORE INTO tickets (id, project_id, title, description) VALUES (?, ?, ?, ?)",
        (ticket_id, "proj-1", "Test", "desc"),
    )
    db.execute(
        "INSERT OR IGNORE INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'implementer', 'read-write', 1, 'success', 'p', '{}', datetime('now'))",
        (run_id, ticket_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# check_workspace_drift
# ---------------------------------------------------------------------------


class TestCheckWorkspaceDrift:
    def test_no_drift(self, db, git_repo):
        _insert_run(db)
        # Modify file, capture diff, then check — workspace matches stored diff
        (git_repo / "file.txt").write_text("changed\n")
        diff = capture_diff(git_repo)
        persist_run_diff(db, "run-1", diff)

        result = check_workspace_drift(db, git_repo, "run-1")
        assert result.has_drift is False
        assert result.expected_diff == result.actual_diff

    def test_drift_detected(self, db, git_repo):
        _insert_run(db)
        # Capture diff with one change, then modify further
        (git_repo / "file.txt").write_text("changed\n")
        diff = capture_diff(git_repo)
        persist_run_diff(db, "run-1", diff)

        # Now make the workspace differ
        (git_repo / "file.txt").write_text("changed again\n")

        result = check_workspace_drift(db, git_repo, "run-1")
        assert result.has_drift is True
        assert result.expected_diff != result.actual_diff

    def test_drift_from_reverted_changes(self, db, git_repo):
        _insert_run(db)
        # Capture diff with changes
        (git_repo / "file.txt").write_text("changed\n")
        diff = capture_diff(git_repo)
        persist_run_diff(db, "run-1", diff)

        # Revert to original — workspace now has no diff but stored diff is non-empty
        (git_repo / "file.txt").write_text("original\n")

        result = check_workspace_drift(db, git_repo, "run-1")
        assert result.has_drift is True


# ---------------------------------------------------------------------------
# handle_drift
# ---------------------------------------------------------------------------


class TestHandleDrift:
    def test_no_drift_noop(self, db, git_repo):
        _insert_run(db)
        (git_repo / "file.txt").write_text("changed\n")
        diff = capture_diff(git_repo)
        persist_run_diff(db, "run-1", diff)

        # Should not raise
        handle_drift(db, "run-1", git_repo, allow_drift=False)

    def test_drift_raises_without_allow(self, db, git_repo):
        _insert_run(db)
        (git_repo / "file.txt").write_text("changed\n")
        diff = capture_diff(git_repo)
        persist_run_diff(db, "run-1", diff)

        (git_repo / "file.txt").write_text("drifted\n")

        with pytest.raises(WorkspaceDriftError, match="--allow-drift"):
            handle_drift(db, "run-1", git_repo, allow_drift=False)

    def test_drift_recaptures_with_allow(self, db, git_repo):
        _insert_run(db)
        (git_repo / "file.txt").write_text("changed\n")
        diff = capture_diff(git_repo)
        persist_run_diff(db, "run-1", diff)

        # Drift the workspace
        (git_repo / "file.txt").write_text("drifted\n")

        handle_drift(db, "run-1", git_repo, allow_drift=True)

        # Stored diff should now match current workspace
        new_diff = get_run_diff(db, "run-1")
        current = capture_diff(git_repo)
        assert new_diff.diff_text == current.diff_text
        assert "drifted" in new_diff.diff_text

    def test_allow_drift_no_drift_noop(self, db, git_repo):
        _insert_run(db)
        (git_repo / "file.txt").write_text("changed\n")
        diff = capture_diff(git_repo)
        persist_run_diff(db, "run-1", diff)

        # No drift — allow_drift=True should still be a no-op
        handle_drift(db, "run-1", git_repo, allow_drift=True)
        stored = get_run_diff(db, "run-1")
        assert stored.diff_text == diff.diff_text


# ---------------------------------------------------------------------------
# capture_review_baseline
# ---------------------------------------------------------------------------


class TestCaptureReviewBaseline:
    def test_baseline_created(self, db, git_repo):
        _insert_run(db, "review-1")
        (git_repo / "file.txt").write_text("impl changes\n")

        capture_review_baseline(db, git_repo, "review-1")

        row = db.execute(
            "SELECT baseline_diff, baseline_status, post_diff, violation "
            "FROM review_baselines WHERE run_id = ?",
            ("review-1",),
        ).fetchone()
        assert row is not None
        assert "impl changes" in row["baseline_diff"]
        assert row["baseline_status"] == "captured"
        assert row["post_diff"] is None
        assert row["violation"] == 0

    def test_baseline_captures_empty_workspace(self, db, git_repo):
        _insert_run(db, "review-2")
        # No modifications — empty diff
        capture_review_baseline(db, git_repo, "review-2")

        row = db.execute(
            "SELECT baseline_diff FROM review_baselines WHERE run_id = ?",
            ("review-2",),
        ).fetchone()
        assert row["baseline_diff"] == ""


# ---------------------------------------------------------------------------
# check_review_violation
# ---------------------------------------------------------------------------


class TestCheckReviewViolation:
    def test_no_violation(self, db, git_repo):
        _insert_run(db, "review-1")
        (git_repo / "file.txt").write_text("impl changes\n")
        capture_review_baseline(db, git_repo, "review-1")

        # Workspace unchanged after review
        violation = check_review_violation(db, git_repo, "review-1")
        assert violation is False

        row = db.execute(
            "SELECT post_diff, violation FROM review_baselines WHERE run_id = ?",
            ("review-1",),
        ).fetchone()
        assert row["violation"] == 0
        assert row["post_diff"] is not None

    def test_violation_detected(self, db, git_repo):
        _insert_run(db, "review-1")
        (git_repo / "file.txt").write_text("impl changes\n")
        capture_review_baseline(db, git_repo, "review-1")

        # Reviewer modifies a tracked file
        (git_repo / "file.txt").write_text("reviewer touched this\n")

        violation = check_review_violation(db, git_repo, "review-1")
        assert violation is True

        row = db.execute(
            "SELECT post_diff, violation FROM review_baselines WHERE run_id = ?",
            ("review-1",),
        ).fetchone()
        assert row["violation"] == 1
        assert "reviewer touched this" in row["post_diff"]

    def test_no_baseline_raises(self, db, git_repo):
        with pytest.raises(KeyError, match="No review baseline"):
            check_review_violation(db, git_repo, "nonexistent")

    def test_violation_from_new_staged_file(self, db, git_repo):
        _insert_run(db, "review-1")
        (git_repo / "file.txt").write_text("impl changes\n")
        capture_review_baseline(db, git_repo, "review-1")

        # Reviewer stages a new file
        (git_repo / "extra.txt").write_text("extra\n")
        subprocess.run(
            ["git", "add", "extra.txt"], cwd=git_repo, check=True, capture_output=True
        )

        violation = check_review_violation(db, git_repo, "review-1")
        assert violation is True

    def test_violation_from_deleted_changes(self, db, git_repo):
        _insert_run(db, "review-1")
        (git_repo / "file.txt").write_text("impl changes\n")
        capture_review_baseline(db, git_repo, "review-1")

        # Reviewer reverts the implementation changes
        (git_repo / "file.txt").write_text("original\n")

        violation = check_review_violation(db, git_repo, "review-1")
        assert violation is True
