"""Tests for permission-denied run outcomes (T01).

Covers:
- adapter detection and normalization of permission denials
- fixture-based tests for edit-only and mixed denial envelopes
- permission denials with is_error and non-zero exit code
- implementer pipeline routing to human-gate without consuming retries
- reviewer pipeline routing to human-gate without consuming retries
- resume handling of permission-denied runs
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from capsaicin.adapters.claude_code import ClaudeCodeAdapter
from capsaicin.adapters.types import RunRequest, RunResult, AcceptanceCriterion
from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.orchestrator import get_state
from capsaicin.resume import (
    _handle_finished_impl_run,
    _handle_finished_review_run,
)
from capsaicin.ticket_add import _get_project_id, add_ticket_inline
from capsaicin.ticket_run import run_implementation_pipeline
from capsaicin.ticket_review import run_review_pipeline
from capsaicin.config import load_config
from capsaicin.adapters.base import BaseAdapter

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(**overrides) -> RunRequest:
    defaults = {
        "run_id": "run-001",
        "role": "implementer",
        "mode": "read-write",
        "working_directory": "/tmp",
        "prompt": "Implement the feature",
        "timeout_seconds": 60,
    }
    defaults.update(overrides)
    return RunRequest(**defaults)


def _reviewer_request(**overrides) -> RunRequest:
    defaults = {
        "run_id": "run-rev-001",
        "role": "reviewer",
        "mode": "read-only",
        "working_directory": "/tmp",
        "prompt": "Review the diff",
        "timeout_seconds": 60,
        "acceptance_criteria": [
            AcceptanceCriterion(id="ac-1", description="Login returns JWT"),
        ],
        "adapter_config": {
            "allowed_tools": ["Read", "Glob", "Grep", "Bash"],
        },
    }
    defaults.update(overrides)
    return RunRequest(**defaults)


def _mock_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    def side_effect(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=returncode, stdout=stdout, stderr=stderr
        )

    return side_effect


class PermissionDeniedAdapter(BaseAdapter):
    """Adapter that returns a permission_denied result."""

    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="permission_denied",
            duration_seconds=1.0,
            result_text="Permission denied — please grant write access.",
            raw_stdout="mock stdout",
            raw_stderr="",
            adapter_metadata={
                "permission_denials": [
                    {
                        "tool_name": "Edit",
                        "tool_use_id": "t1",
                        "tool_input": {"file_path": "/a.py"},
                    }
                ],
                "normalized_denials": [
                    {"tool_name": "Edit", "tool_use_id": "t1", "file_path": "/a.py"}
                ],
            },
        )


# ---------------------------------------------------------------------------
# Adapter: permission denial detection
# ---------------------------------------------------------------------------


class TestPermissionDenialDetection:
    def test_edit_only_fixture_returns_permission_denied(self):
        envelope = (
            FIXTURES / "claude_envelope_permission_denied_edit_only.json"
        ).read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())

        assert result.exit_status == "permission_denied"
        assert result.run_id == "run-001"
        assert result.raw_stdout == envelope

        # Metadata preserved
        meta = result.adapter_metadata
        assert "permission_denials" in meta
        assert len(meta["permission_denials"]) == 3

        # Normalized denials present
        assert "normalized_denials" in meta
        normalized = meta["normalized_denials"]
        assert len(normalized) == 3
        for entry in normalized:
            assert entry["tool_name"] == "Edit"
            assert "file_path" in entry
            assert "tool_use_id" in entry

    def test_mixed_fixture_returns_permission_denied(self):
        envelope = (
            FIXTURES / "claude_envelope_permission_denied_mixed.json"
        ).read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())

        assert result.exit_status == "permission_denied"
        meta = result.adapter_metadata
        normalized = meta["normalized_denials"]
        assert len(normalized) == 4

        # First denial is Bash with command
        bash_entries = [d for d in normalized if d["tool_name"] == "Bash"]
        assert len(bash_entries) == 1
        assert "command" in bash_entries[0]

        # Remaining are Edit with file_path
        edit_entries = [d for d in normalized if d["tool_name"] == "Edit"]
        assert len(edit_entries) == 3
        for entry in edit_entries:
            assert "file_path" in entry

    def test_empty_denials_list_is_not_permission_denied(self):
        """An empty permission_denials list should NOT trigger permission_denied."""
        envelope = (FIXTURES / "claude_implementer_success.json").read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())
        assert result.exit_status == "success"

    def test_result_text_preserved_on_permission_denied(self):
        envelope = (
            FIXTURES / "claude_envelope_permission_denied_edit_only.json"
        ).read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())
        assert "permission" in result.result_text.lower()

    def test_reviewer_mode_with_permission_denied(self):
        """Permission denials override reviewer structured-output parsing."""
        envelope = (
            FIXTURES / "claude_envelope_permission_denied_edit_only.json"
        ).read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_reviewer_request())
        assert result.exit_status == "permission_denied"
        assert result.structured_result is None

    def test_repeated_denials_preserved_as_distinct_attempts(self):
        """Each denial attempt is kept, not deduplicated."""
        envelope = (
            FIXTURES / "claude_envelope_permission_denied_edit_only.json"
        ).read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())
        # The edit-only fixture has 3 denials for the same file — all preserved
        assert len(result.adapter_metadata["normalized_denials"]) == 3
        assert len(result.adapter_metadata["permission_denials"]) == 3

    def test_raw_envelope_preserved_on_permission_denied(self):
        envelope = (
            FIXTURES / "claude_envelope_permission_denied_mixed.json"
        ).read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())
        parsed = json.loads(result.raw_stdout)
        assert "permission_denials" in parsed


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestDenialNormalization:
    def test_edit_normalization(self):
        raw = [
            {
                "tool_name": "Edit",
                "tool_use_id": "t1",
                "tool_input": {
                    "file_path": "/a.py",
                    "old_string": "x",
                    "new_string": "y",
                },
            }
        ]
        normalized = ClaudeCodeAdapter._normalize_denials(raw)
        assert len(normalized) == 1
        assert normalized[0] == {
            "tool_name": "Edit",
            "tool_use_id": "t1",
            "file_path": "/a.py",
        }

    def test_write_normalization(self):
        raw = [
            {
                "tool_name": "Write",
                "tool_use_id": "t2",
                "tool_input": {"file_path": "/b.py", "content": "stuff"},
            }
        ]
        normalized = ClaudeCodeAdapter._normalize_denials(raw)
        assert normalized[0]["file_path"] == "/b.py"
        assert "content" not in normalized[0]

    def test_bash_normalization(self):
        raw = [
            {
                "tool_name": "Bash",
                "tool_use_id": "t3",
                "tool_input": {"command": "rm -rf /"},
            }
        ]
        normalized = ClaudeCodeAdapter._normalize_denials(raw)
        assert normalized[0]["command"] == "rm -rf /"
        assert "file_path" not in normalized[0]

    def test_unknown_tool_normalization(self):
        raw = [
            {
                "tool_name": "SomeTool",
                "tool_use_id": "t4",
                "tool_input": {"whatever": "value"},
            }
        ]
        normalized = ClaudeCodeAdapter._normalize_denials(raw)
        assert normalized[0] == {"tool_name": "SomeTool", "tool_use_id": "t4"}


# ---------------------------------------------------------------------------
# Pipeline integration: implementer permission denied
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_env(tmp_path):
    """Set up a project with a git repo, returning context dict."""
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
    (repo / "impl.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    project_dir = init_project("test-proj", str(repo))
    conn = get_connection(project_dir / "capsaicin.db")
    project_id = _get_project_id(conn)
    log_path = project_dir / "activity.log"
    config = load_config(project_dir / "config.toml")

    yield {
        "repo": repo,
        "project_dir": project_dir,
        "conn": conn,
        "project_id": project_id,
        "log_path": log_path,
        "config": config,
    }
    conn.close()


def _add_ticket(env, title="Test ticket", desc="Do something"):
    return add_ticket_inline(
        env["conn"], env["project_id"], title, desc, [], env["log_path"]
    )


def _get_ticket(conn, ticket_id):
    return dict(
        conn.execute(
            "SELECT id, project_id, title, description, status, "
            "current_cycle, current_impl_attempt, current_review_attempt "
            "FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
    )


class TestImplPermissionDeniedPipeline:
    def test_transitions_to_human_gate(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        final = run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        assert final == "human-gate"
        row = (
            env["conn"]
            .execute("SELECT status, gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "human-gate"
        assert row["gate_reason"] == "permission_denied"

    def test_does_not_consume_retries(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        # Only one run should exist (no retries)
        runs = (
            env["conn"]
            .execute("SELECT * FROM agent_runs WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(runs) == 1
        assert runs[0]["exit_status"] == "permission_denied"

    def test_orchestrator_awaiting_human(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"

    def test_run_record_persisted_with_permission_denied(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        run = (
            env["conn"]
            .execute(
                "SELECT exit_status, adapter_metadata, finished_at "
                "FROM agent_runs WHERE ticket_id = ?",
                (tid,),
            )
            .fetchone()
        )
        assert run["exit_status"] == "permission_denied"
        assert run["finished_at"] is not None
        meta = json.loads(run["adapter_metadata"])
        assert "normalized_denials" in meta

    def test_activity_log_contains_permission_denied(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        log = env["log_path"].read_text()
        assert "PERMISSION_DENIED" in log

    def test_state_transitions_recorded(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = PermissionDeniedAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        transitions = (
            env["conn"]
            .execute(
                "SELECT from_status, to_status FROM state_transitions "
                "WHERE ticket_id = ? ORDER BY id",
                (tid,),
            )
            .fetchall()
        )
        statuses = [(t["from_status"], t["to_status"]) for t in transitions]
        assert ("ready", "implementing") in statuses
        assert ("implementing", "human-gate") in statuses


# ---------------------------------------------------------------------------
# Schema: permission_denied in agent_runs and gate_reason in tickets
# ---------------------------------------------------------------------------


class TestSchemaAcceptsPermissionDenied:
    def test_exit_status_permission_denied_accepted(self, project_env):
        """The agent_runs exit_status CHECK should accept 'permission_denied'."""
        env = project_env
        tid = _add_ticket(env)
        # Direct insert to verify CHECK constraint
        env["conn"].execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, attempt_number, "
            "exit_status, prompt, run_request, started_at) "
            "VALUES (?, ?, 'implementer', 'read-write', 1, 1, "
            "'permission_denied', 'test', '{}', '2024-01-01T00:00:00Z')",
            ("test-run-pd", tid),
        )
        env["conn"].commit()
        row = (
            env["conn"]
            .execute("SELECT exit_status FROM agent_runs WHERE id = 'test-run-pd'")
            .fetchone()
        )
        assert row["exit_status"] == "permission_denied"

    def test_gate_reason_permission_denied_accepted(self, project_env):
        """The tickets gate_reason CHECK should accept 'permission_denied'."""
        env = project_env
        tid = _add_ticket(env)
        env["conn"].execute(
            "UPDATE tickets SET gate_reason = 'permission_denied', "
            "status = 'human-gate' WHERE id = ?",
            (tid,),
        )
        env["conn"].commit()
        row = (
            env["conn"]
            .execute("SELECT gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["gate_reason"] == "permission_denied"


# ---------------------------------------------------------------------------
# Adapter: permission denied beats is_error and non-zero exit
# ---------------------------------------------------------------------------


class TestPermissionDeniedPrecedence:
    def test_permission_denied_with_is_error_true(self):
        """permission_denials should take precedence over is_error: true."""
        envelope = json.loads(
            (FIXTURES / "claude_envelope_permission_denied_edit_only.json").read_text()
        )
        envelope["is_error"] = True
        envelope_str = json.dumps(envelope)

        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope_str)):
            result = adapter.execute(_request())

        assert result.exit_status == "permission_denied"
        assert "normalized_denials" in result.adapter_metadata

    def test_permission_denied_with_nonzero_exit(self):
        """permission_denials should take precedence over non-zero exit code."""
        envelope_str = (
            FIXTURES / "claude_envelope_permission_denied_mixed.json"
        ).read_text()
        adapter = ClaudeCodeAdapter()
        with patch(
            "subprocess.run", side_effect=_mock_run(stdout=envelope_str, returncode=1)
        ):
            result = adapter.execute(_request())

        assert result.exit_status == "permission_denied"
        assert len(result.adapter_metadata["normalized_denials"]) == 4

    def test_permission_denied_with_both_is_error_and_nonzero_exit(self):
        """permission_denials should take precedence over both signals combined."""
        envelope = json.loads(
            (FIXTURES / "claude_envelope_permission_denied_mixed.json").read_text()
        )
        envelope["is_error"] = True
        envelope_str = json.dumps(envelope)

        adapter = ClaudeCodeAdapter()
        with patch(
            "subprocess.run", side_effect=_mock_run(stdout=envelope_str, returncode=1)
        ):
            result = adapter.execute(_request())

        assert result.exit_status == "permission_denied"

    def test_is_error_without_denials_still_returns_failure(self):
        """is_error: true with no denials should still be failure."""
        envelope = json.dumps({"is_error": True, "result": "something broke"})
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"

    def test_nonzero_exit_without_denials_still_returns_failure(self):
        """Non-zero exit with no denials should still be failure."""
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout="{}", returncode=1)):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"


# ---------------------------------------------------------------------------
# Reviewer pipeline: permission denied → human-gate
# ---------------------------------------------------------------------------


class PermissionDeniedReviewAdapter(BaseAdapter):
    """Adapter that returns permission_denied for reviewer runs."""

    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="permission_denied",
            duration_seconds=1.0,
            result_text="Permission denied — please grant read access.",
            raw_stdout="mock stdout",
            raw_stderr="",
            adapter_metadata={
                "permission_denials": [
                    {
                        "tool_name": "Bash",
                        "tool_use_id": "t1",
                        "tool_input": {"command": "ls"},
                    }
                ],
                "normalized_denials": [
                    {"tool_name": "Bash", "tool_use_id": "t1", "command": "ls"}
                ],
            },
        )


class DiffProducingImplAdapter(BaseAdapter):
    """Adapter that modifies a file in the repo before returning success."""

    def __init__(self, repo_path, filename="impl.txt", content="implemented\n"):
        self.repo_path = repo_path
        self.filename = filename
        self.content = content
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        (self.repo_path / self.filename).write_text(self.content)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            adapter_metadata={},
        )


def _run_impl_to_in_review(env, ticket_id=None):
    """Run implementation pipeline to get a ticket into in-review status."""
    if ticket_id is None:
        ticket_id = _add_ticket(env)
    ticket = _get_ticket(env["conn"], ticket_id)
    adapter = DiffProducingImplAdapter(env["repo"])
    final = run_implementation_pipeline(
        conn=env["conn"],
        project_id=env["project_id"],
        ticket=ticket,
        config=env["config"],
        adapter=adapter,
        log_path=env["log_path"],
    )
    assert final == "in-review"
    return ticket_id


class TestReviewerPermissionDeniedPipeline:
    def test_transitions_to_human_gate(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = dict(
            env["conn"]
            .execute(
                "SELECT id, project_id, title, description, status, "
                "current_cycle, current_impl_attempt, current_review_attempt "
                "FROM tickets WHERE id = ?",
                (tid,),
            )
            .fetchone()
        )

        review_adapter = PermissionDeniedReviewAdapter()
        final = run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=review_adapter,
            log_path=env["log_path"],
        )

        assert final == "human-gate"
        row = (
            env["conn"]
            .execute("SELECT status, gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "human-gate"
        assert row["gate_reason"] == "permission_denied"

    def test_does_not_consume_retries(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = dict(
            env["conn"]
            .execute(
                "SELECT id, project_id, title, description, status, "
                "current_cycle, current_impl_attempt, current_review_attempt "
                "FROM tickets WHERE id = ?",
                (tid,),
            )
            .fetchone()
        )

        review_adapter = PermissionDeniedReviewAdapter()
        run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=review_adapter,
            log_path=env["log_path"],
        )

        # Only one reviewer run (the impl run + one review run total)
        reviewer_runs = (
            env["conn"]
            .execute(
                "SELECT * FROM agent_runs WHERE ticket_id = ? AND role = 'reviewer'",
                (tid,),
            )
            .fetchall()
        )
        assert len(reviewer_runs) == 1
        assert reviewer_runs[0]["exit_status"] == "permission_denied"

    def test_orchestrator_awaiting_human(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = dict(
            env["conn"]
            .execute(
                "SELECT id, project_id, title, description, status, "
                "current_cycle, current_impl_attempt, current_review_attempt "
                "FROM tickets WHERE id = ?",
                (tid,),
            )
            .fetchone()
        )

        review_adapter = PermissionDeniedReviewAdapter()
        run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=review_adapter,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"

    def test_activity_log_contains_permission_denied(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = dict(
            env["conn"]
            .execute(
                "SELECT id, project_id, title, description, status, "
                "current_cycle, current_impl_attempt, current_review_attempt "
                "FROM tickets WHERE id = ?",
                (tid,),
            )
            .fetchone()
        )

        review_adapter = PermissionDeniedReviewAdapter()
        run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=review_adapter,
            log_path=env["log_path"],
        )

        log = env["log_path"].read_text()
        assert "PERMISSION_DENIED" in log


# ---------------------------------------------------------------------------
# Resume: finished permission-denied runs → human-gate
# ---------------------------------------------------------------------------


class TestResumePermissionDeniedImpl:
    def test_resume_finished_impl_permission_denied(self, project_env):
        """A finished implementer run with permission_denied should
        route to human-gate on resume, not retry."""
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        # Run the pipeline with a permission-denied adapter
        adapter = PermissionDeniedAdapter()
        final = run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )
        assert final == "human-gate"

        # Verify the run record
        run = (
            env["conn"]
            .execute(
                "SELECT id, exit_status, finished_at FROM agent_runs "
                "WHERE ticket_id = ? AND role = 'implementer'",
                (tid,),
            )
            .fetchone()
        )
        assert run["exit_status"] == "permission_denied"
        assert run["finished_at"] is not None

        # Now simulate resume by calling _handle_finished_impl_run
        # First, put ticket back in implementing to simulate the resume scenario
        env["conn"].execute(
            "UPDATE tickets SET status = 'implementing' WHERE id = ?", (tid,)
        )
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'running', "
            "active_ticket_id = ?, active_run_id = ? WHERE project_id = ?",
            (tid, run["id"], env["project_id"]),
        )
        env["conn"].commit()

        run_dict = dict(
            env["conn"]
            .execute(
                "SELECT id, ticket_id, role, mode, exit_status, verdict, "
                "duration_seconds, raw_stdout, raw_stderr, structured_result, "
                "adapter_metadata, started_at, finished_at "
                "FROM agent_runs WHERE id = ?",
                (run["id"],),
            )
            .fetchone()
        )

        final_status = _handle_finished_impl_run(
            conn=env["conn"],
            project_id=env["project_id"],
            run=run_dict,
            config=env["config"],
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        row = (
            env["conn"]
            .execute("SELECT status, gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "human-gate"
        assert row["gate_reason"] == "permission_denied"


class TestResumePermissionDeniedReview:
    def test_resume_finished_review_permission_denied(self, project_env):
        """A finished reviewer run with permission_denied should
        route to human-gate on resume, not retry."""
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = dict(
            env["conn"]
            .execute(
                "SELECT id, project_id, title, description, status, "
                "current_cycle, current_impl_attempt, current_review_attempt "
                "FROM tickets WHERE id = ?",
                (tid,),
            )
            .fetchone()
        )

        # Run the review pipeline with a permission-denied adapter
        review_adapter = PermissionDeniedReviewAdapter()
        final = run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=review_adapter,
            log_path=env["log_path"],
        )
        assert final == "human-gate"

        # Get the reviewer run record
        review_run = (
            env["conn"]
            .execute(
                "SELECT id, exit_status, finished_at FROM agent_runs "
                "WHERE ticket_id = ? AND role = 'reviewer'",
                (tid,),
            )
            .fetchone()
        )
        assert review_run["exit_status"] == "permission_denied"
        assert review_run["finished_at"] is not None

        # Simulate resume: put ticket back in in-review
        env["conn"].execute(
            "UPDATE tickets SET status = 'in-review', gate_reason = NULL WHERE id = ?",
            (tid,),
        )
        env["conn"].execute(
            "UPDATE orchestrator_state SET status = 'running', "
            "active_ticket_id = ?, active_run_id = ? WHERE project_id = ?",
            (tid, review_run["id"], env["project_id"]),
        )
        env["conn"].commit()

        run_dict = dict(
            env["conn"]
            .execute(
                "SELECT id, ticket_id, role, mode, exit_status, verdict, "
                "duration_seconds, raw_stdout, raw_stderr, structured_result, "
                "adapter_metadata, started_at, finished_at "
                "FROM agent_runs WHERE id = ?",
                (review_run["id"],),
            )
            .fetchone()
        )

        final_status = _handle_finished_review_run(
            conn=env["conn"],
            project_id=env["project_id"],
            run=run_dict,
            config=env["config"],
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        row = (
            env["conn"]
            .execute("SELECT status, gate_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "human-gate"
        assert row["gate_reason"] == "permission_denied"
