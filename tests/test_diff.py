"""Tests for the diff capture module (T14)."""

from __future__ import annotations

import subprocess

import pytest

from capsaicin.db import get_connection, run_migrations
from capsaicin.diff import (
    DiffResult,
    capture_diff,
    diffs_match,
    get_run_diff,
    persist_run_diff,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory database with full schema."""
    conn = get_connection(":memory:")
    run_migrations(conn)
    return conn


@pytest.fixture()
def git_repo(tmp_path):
    """Create a temporary git repo with one committed file."""
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
    # Create and commit a tracked file
    (repo / "hello.txt").write_text("hello\n")
    subprocess.run(
        ["git", "add", "hello.txt"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# DiffResult dataclass
# ---------------------------------------------------------------------------


class TestDiffResult:
    def test_is_empty_true(self):
        dr = DiffResult(diff_text="", files_changed=[])
        assert dr.is_empty is True

    def test_is_empty_whitespace(self):
        dr = DiffResult(diff_text="   \n  ", files_changed=[])
        assert dr.is_empty is True

    def test_is_empty_false(self):
        dr = DiffResult(diff_text="--- a/foo\n+++ b/foo\n", files_changed=["foo"])
        assert dr.is_empty is False


# ---------------------------------------------------------------------------
# capture_diff
# ---------------------------------------------------------------------------


class TestCaptureDiff:
    def test_no_changes(self, git_repo):
        result = capture_diff(git_repo)
        assert result.is_empty is True
        assert result.files_changed == []

    def test_modified_tracked_file(self, git_repo):
        (git_repo / "hello.txt").write_text("hello world\n")
        result = capture_diff(git_repo)
        assert result.is_empty is False
        assert "hello.txt" in result.files_changed
        assert "hello world" in result.diff_text

    def test_multiple_modified_files(self, git_repo):
        # Add and commit a second file
        (git_repo / "second.txt").write_text("original\n")
        subprocess.run(
            ["git", "add", "second.txt"], cwd=git_repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add second"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
        # Modify both files
        (git_repo / "hello.txt").write_text("changed hello\n")
        (git_repo / "second.txt").write_text("changed second\n")
        result = capture_diff(git_repo)
        assert result.is_empty is False
        assert set(result.files_changed) == {"hello.txt", "second.txt"}

    def test_untracked_files_excluded(self, git_repo):
        (git_repo / "untracked.txt").write_text("not tracked\n")
        result = capture_diff(git_repo)
        assert result.is_empty is True
        assert "untracked.txt" not in result.files_changed

    def test_new_staged_file(self, git_repo):
        """A file that is staged but not committed should appear in git diff HEAD."""
        (git_repo / "staged.txt").write_text("staged content\n")
        subprocess.run(
            ["git", "add", "staged.txt"], cwd=git_repo, check=True, capture_output=True
        )
        result = capture_diff(git_repo)
        assert result.is_empty is False
        assert "staged.txt" in result.files_changed

    def test_invalid_repo_path(self, tmp_path):
        bad_path = tmp_path / "not-a-repo"
        bad_path.mkdir()
        with pytest.raises(RuntimeError, match="git diff HEAD failed"):
            capture_diff(bad_path)


# ---------------------------------------------------------------------------
# persist_run_diff / get_run_diff
# ---------------------------------------------------------------------------


class TestPersistAndGet:
    def _insert_run(self, conn, run_id="run-1", ticket_id="ticket-1"):
        """Insert minimal project, ticket, and agent_run rows for FK satisfaction."""
        conn.execute(
            "INSERT INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
            ("proj-1", "test", "/tmp/repo"),
        )
        conn.execute(
            "INSERT INTO tickets (id, project_id, title, description) VALUES (?, ?, ?, ?)",
            (ticket_id, "proj-1", "Test", "A test ticket"),
        )
        conn.execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, run_request, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                run_id,
                ticket_id,
                "implementer",
                "read-write",
                1,
                "success",
                "do stuff",
                "{}",
            ),
        )
        conn.commit()

    def test_round_trip(self, db):
        self._insert_run(db)
        diff = DiffResult(
            diff_text="--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-old\n+new\n",
            files_changed=["foo"],
        )
        persist_run_diff(db, "run-1", diff)
        retrieved = get_run_diff(db, "run-1")
        assert retrieved.diff_text == diff.diff_text
        assert retrieved.files_changed == diff.files_changed
        assert retrieved.is_empty is False

    def test_empty_diff_round_trip(self, db):
        self._insert_run(db)
        diff = DiffResult(diff_text="", files_changed=[])
        persist_run_diff(db, "run-1", diff)
        retrieved = get_run_diff(db, "run-1")
        assert retrieved.diff_text == ""
        assert retrieved.files_changed == []
        assert retrieved.is_empty is True

    def test_get_nonexistent_raises(self, db):
        with pytest.raises(KeyError, match="No diff found"):
            get_run_diff(db, "no-such-run")

    def test_multiple_files_changed(self, db):
        self._insert_run(db)
        diff = DiffResult(
            diff_text="diff content",
            files_changed=["a.py", "b.py", "c/d.py"],
        )
        persist_run_diff(db, "run-1", diff)
        retrieved = get_run_diff(db, "run-1")
        assert retrieved.files_changed == ["a.py", "b.py", "c/d.py"]


# ---------------------------------------------------------------------------
# diffs_match
# ---------------------------------------------------------------------------


class TestDiffsMatch:
    def test_identical(self):
        text = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n"
        assert diffs_match(text, text) is True

    def test_trailing_whitespace_is_drift(self):
        a = "--- a/f  \n+++ b/f\n"
        b = "--- a/f\n+++ b/f\n"
        assert diffs_match(a, b) is False

    def test_different_content(self):
        a = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n"
        b = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+other\n"
        assert diffs_match(a, b) is False

    def test_both_empty(self):
        assert diffs_match("", "") is True

    def test_one_empty(self):
        assert diffs_match("some diff", "") is False

    def test_trailing_newline_difference_is_drift(self):
        a = "line1\nline2\n"
        b = "line1\nline2"
        # Exact comparison — a trailing newline difference is real drift
        assert diffs_match(a, b) is False
