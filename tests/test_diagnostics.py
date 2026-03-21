"""Tests for run-outcome diagnostics (T02).

Covers:
- diagnostic message generation for empty implementations
- diagnostic message generation for permission-denied runs
- agent result text extraction and truncation
- denial summary with tool names
- human-gate context integration (resume.build_human_gate_context)
- loop stop message consistency
- CLI ticket run output
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import RunRequest, RunResult
from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.diagnostics import (
    _denial_summary,
    _extract_result_text_from_raw,
    _truncate,
    build_run_outcome_message,
)
from capsaicin.init import init_project
from capsaicin.loop import run_loop
from capsaicin.resume import build_human_gate_context
from capsaicin.ticket_add import _get_project_id, add_ticket_inline
from capsaicin.ticket_run import run_implementation_pipeline

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockAdapter(BaseAdapter):
    """Adapter that returns success without modifying files (empty impl)."""

    def __init__(self, exit_status="success", result_text="", raw_stdout=""):
        self._exit_status = exit_status
        self._result_text = result_text
        self._raw_stdout = raw_stdout
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status=self._exit_status,
            duration_seconds=1.0,
            result_text=self._result_text,
            raw_stdout=self._raw_stdout,
            raw_stderr="",
            adapter_metadata={},
        )


class PermissionDeniedAdapter(BaseAdapter):
    """Adapter that returns permission_denied with realistic metadata."""

    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        envelope = {
            "result": "Please grant write permission so I can proceed.",
            "is_error": False,
            "permission_denials": [
                {
                    "tool_name": "Edit",
                    "tool_use_id": "t1",
                    "tool_input": {"file_path": "/app/main.py"},
                },
                {
                    "tool_name": "Edit",
                    "tool_use_id": "t2",
                    "tool_input": {"file_path": "/app/utils.py"},
                },
                {
                    "tool_name": "Bash",
                    "tool_use_id": "t3",
                    "tool_input": {"command": "mkdir build"},
                },
            ],
        }
        return RunResult(
            run_id=request.run_id,
            exit_status="permission_denied",
            duration_seconds=1.5,
            result_text="Please grant write permission so I can proceed.",
            raw_stdout=json.dumps(envelope),
            raw_stderr="",
            adapter_metadata={
                "permission_denials": envelope["permission_denials"],
                "normalized_denials": [
                    {
                        "tool_name": "Edit",
                        "tool_use_id": "t1",
                        "file_path": "/app/main.py",
                    },
                    {
                        "tool_name": "Edit",
                        "tool_use_id": "t2",
                        "file_path": "/app/utils.py",
                    },
                    {
                        "tool_name": "Bash",
                        "tool_use_id": "t3",
                        "command": "mkdir build",
                    },
                ],
            },
        )


class DiffProducingAdapter(BaseAdapter):
    """Adapter that modifies a file."""

    def __init__(self, repo_path):
        self.repo_path = repo_path
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        (self.repo_path / "impl.txt").write_text("implemented\n")
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            adapter_metadata={},
        )


@pytest.fixture()
def project_env(tmp_path):
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


# ---------------------------------------------------------------------------
# Unit tests: extraction helpers
# ---------------------------------------------------------------------------


class TestExtractResultText:
    def test_extracts_from_valid_envelope(self):
        raw = json.dumps({"result": "I fixed the bug."})
        assert _extract_result_text_from_raw(raw) == "I fixed the bug."

    def test_empty_on_invalid_json(self):
        assert _extract_result_text_from_raw("not json") == ""

    def test_empty_on_none(self):
        assert _extract_result_text_from_raw(None) == ""

    def test_empty_on_missing_result_field(self):
        raw = json.dumps({"is_error": False})
        assert _extract_result_text_from_raw(raw) == ""

    def test_empty_on_non_string_result(self):
        raw = json.dumps({"result": 42})
        assert _extract_result_text_from_raw(raw) == ""

    def test_extracts_from_real_fixture(self):
        raw = (
            FIXTURES / "claude_envelope_permission_denied_edit_only.json"
        ).read_text()
        text = _extract_result_text_from_raw(raw)
        assert "permission" in text.lower()


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_long_text_truncated(self):
        text = "a" * 500
        result = _truncate(text, 50)
        assert len(result) <= 51  # 50 + ellipsis
        assert result.endswith("…")

    def test_exact_length_unchanged(self):
        text = "a" * 50
        assert _truncate(text, 50) == text


class TestDenialSummary:
    def test_with_normalized_denials(self):
        meta = {
            "normalized_denials": [
                {"tool_name": "Edit", "tool_use_id": "t1"},
                {"tool_name": "Bash", "tool_use_id": "t2"},
                {"tool_name": "Edit", "tool_use_id": "t3"},
            ]
        }
        result = _denial_summary(meta)
        assert "3 denied" in result
        assert "Bash" in result
        assert "Edit" in result

    def test_with_raw_denials_fallback(self):
        meta = {"permission_denials": [{"tool_name": "Write"}]}
        result = _denial_summary(meta)
        assert "1 denied" in result

    def test_empty_metadata(self):
        assert _denial_summary({}) == ""

    def test_empty_lists(self):
        meta = {"normalized_denials": [], "permission_denials": []}
        assert _denial_summary(meta) == ""


# ---------------------------------------------------------------------------
# Integration: build_run_outcome_message
# ---------------------------------------------------------------------------


class TestBuildRunOutcomeMessage:
    def test_permission_denied_message(self, project_env):
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

        msg = build_run_outcome_message(env["conn"], tid)
        assert "blocked by permission" in msg.lower()
        assert "denied action" in msg.lower()
        assert "Bash" in msg
        assert "Edit" in msg
        # Agent text surfaced
        assert "grant write permission" in msg.lower()

    def test_empty_implementation_message(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        # Build an envelope with result text
        envelope = json.dumps(
            {
                "result": "I could not find any changes to make for this ticket.",
                "is_error": False,
            }
        )
        adapter = MockAdapter(
            exit_status="success",
            result_text="I could not find any changes to make for this ticket.",
            raw_stdout=envelope,
        )

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        msg = build_run_outcome_message(env["conn"], tid)
        assert "no changes" in msg.lower()
        assert "could not find" in msg.lower()

    def test_empty_impl_no_result_text(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = MockAdapter(exit_status="success")

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        msg = build_run_outcome_message(env["conn"], tid)
        assert "no changes" in msg.lower()
        # No agent text section when result is empty
        assert "Agent:" not in msg

    def test_success_in_review_returns_empty(self, project_env):
        """A successful run that produced changes should return no diagnostic."""
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = DiffProducingAdapter(env["repo"])

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        msg = build_run_outcome_message(env["conn"], tid)
        assert msg == ""


# ---------------------------------------------------------------------------
# Integration: build_human_gate_context includes diagnostics
# ---------------------------------------------------------------------------


class TestHumanGateContextDiagnostics:
    def test_permission_denied_in_gate_context(self, project_env):
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

        context = build_human_gate_context(env["conn"], tid)
        assert "blocked by permission" in context.lower()
        assert "denied action" in context.lower()
        assert "grant write permission" in context.lower()
        # Still includes standard gate info
        assert "permission_denied" in context
        assert "Awaiting human decision" in context

    def test_empty_impl_in_gate_context(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        envelope = json.dumps(
            {
                "result": "No implementation needed for this ticket.",
                "is_error": False,
            }
        )
        adapter = MockAdapter(
            exit_status="success",
            result_text="No implementation needed for this ticket.",
            raw_stdout=envelope,
        )

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        context = build_human_gate_context(env["conn"], tid)
        assert "no changes" in context.lower()
        assert "No implementation needed" in context
        assert "empty_implementation" in context


# ---------------------------------------------------------------------------
# Integration: loop stop message includes diagnostics
# ---------------------------------------------------------------------------


class TestLoopStopDiagnostics:
    def test_loop_permission_denied_stop_message(self, project_env):
        """Loop should include diagnostic text when stopping at human-gate."""
        env = project_env
        tid = _add_ticket(env)
        adapter = PermissionDeniedAdapter()

        final_status, detail = run_loop(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            impl_adapter=adapter,
            review_adapter=MockAdapter(),  # not reached
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "blocked by permission" in detail.lower()
        assert "denied action" in detail.lower()

    def test_loop_empty_impl_stop_message(self, project_env):
        env = project_env
        tid = _add_ticket(env)

        envelope = json.dumps(
            {
                "result": "Nothing to implement here.",
                "is_error": False,
            }
        )
        adapter = MockAdapter(
            exit_status="success",
            result_text="Nothing to implement here.",
            raw_stdout=envelope,
        )

        final_status, detail = run_loop(
            conn=env["conn"],
            project_id=env["project_id"],
            config=env["config"],
            impl_adapter=adapter,
            review_adapter=MockAdapter(),
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "no changes" in detail.lower()
        assert "Nothing to implement" in detail
