"""Tests for Claude Code adapter — implementer mode (T12)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from capsaicin.adapters.claude_code import ClaudeCodeAdapter
from capsaicin.adapters.types import RunRequest

FIXTURES = Path(__file__).parent / "fixtures"


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


def _mock_run(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    timeout: bool = False,
):
    """Create a mock for subprocess.run."""
    if timeout:

        def side_effect(*args, **kwargs):
            exc = subprocess.TimeoutExpired(cmd=args[0], timeout=60)
            exc.stdout = stdout
            exc.stderr = stderr
            raise exc

        return side_effect
    else:

        def side_effect(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )

        return side_effect


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


class TestCommandConstruction:
    def test_builds_correct_command(self):
        adapter = ClaudeCodeAdapter(command="claude")
        req = _request(prompt="Do the thing")
        cmd = adapter._build_command(req)
        assert cmd == ["claude", "-p", "--output-format", "json", "--", "Do the thing"]

    def test_custom_command(self):
        adapter = ClaudeCodeAdapter(command="/usr/local/bin/claude")
        cmd = adapter._build_command(_request())
        assert cmd[0] == "/usr/local/bin/claude"

    def test_prompt_after_double_dash(self):
        """Prompt must come after -- to avoid being parsed as flags."""
        adapter = ClaudeCodeAdapter()
        cmd = adapter._build_command(_request(prompt="--help me"))
        dash_idx = cmd.index("--")
        assert cmd[dash_idx + 1] == "--help me"


# ---------------------------------------------------------------------------
# Fixture-based tests (captured real envelopes)
# ---------------------------------------------------------------------------


class TestFixtureEnvelopes:
    def test_success_envelope(self):
        envelope = (FIXTURES / "claude_implementer_success.json").read_text()
        adapter = ClaudeCodeAdapter()

        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())

        assert result.exit_status == "success"
        assert result.run_id == "run-001"
        assert result.raw_stdout == envelope
        assert result.duration_seconds > 0

        # adapter_metadata populated
        meta = result.adapter_metadata
        assert meta["session_id"] == "sess-abc123"
        assert meta["num_turns"] == 5
        assert meta["total_cost_usd"] == 0.0342
        assert "usage" in meta
        assert meta["usage"]["input_tokens"] == 3200
        assert "modelUsage" in meta
        assert meta["permission_denials"] == []

    def test_error_envelope_is_failure(self):
        """is_error: true in envelope -> failure even with exit code 0."""
        envelope = (FIXTURES / "claude_implementer_error.json").read_text()
        adapter = ClaudeCodeAdapter()

        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())

        assert result.exit_status == "failure"
        assert result.raw_stdout == envelope

        meta = result.adapter_metadata
        assert meta["session_id"] == "sess-err456"
        assert meta["permission_denials"] == ["Write"]

    def test_raw_envelope_preserved(self):
        """Full raw envelope must be in raw_stdout for debugging."""
        envelope = (FIXTURES / "claude_implementer_success.json").read_text()
        adapter = ClaudeCodeAdapter()

        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())

        parsed = json.loads(result.raw_stdout)
        assert parsed["session_id"] == "sess-abc123"
        assert parsed["result"].startswith("I've implemented")


# ---------------------------------------------------------------------------
# Exit code / timeout behavior
# ---------------------------------------------------------------------------


class TestExitCodeBehavior:
    def test_nonzero_exit_is_failure(self):
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(returncode=1, stderr="err")):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"
        assert result.raw_stderr == "err"

    def test_timeout_returns_timeout(self):
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(timeout=True)):
            result = adapter.execute(_request())
        assert result.exit_status == "timeout"
        assert result.duration_seconds > 0

    def test_timeout_captures_partial_output(self):
        adapter = ClaudeCodeAdapter()
        with patch(
            "subprocess.run",
            side_effect=_mock_run(timeout=True, stdout="partial", stderr="partial_err"),
        ):
            result = adapter.execute(_request())
        assert result.exit_status == "timeout"
        assert result.raw_stdout == "partial"
        assert result.raw_stderr == "partial_err"

    def test_unparseable_stdout_is_failure(self):
        """Non-JSON stdout with exit code 0 should be failure."""
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout="not json")):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"
        assert result.raw_stdout == "not json"

    def test_empty_stdout_is_failure(self):
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout="")):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"

    def test_nonzero_exit_with_envelope_extracts_metadata(self):
        """Even on failure, metadata should be extracted if envelope is parseable."""
        envelope = json.dumps({"session_id": "sess-fail", "is_error": False})
        adapter = ClaudeCodeAdapter()
        with patch(
            "subprocess.run", side_effect=_mock_run(stdout=envelope, returncode=1)
        ):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"
        assert result.adapter_metadata.get("session_id") == "sess-fail"

    # ---------------------------------------------------------------------------
    # Working directory
    # ---------------------------------------------------------------------------

    def test_json_array_stdout_is_failure(self):
        """Valid JSON that is not an object should fail closed, not crash."""
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout="[]")):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"
        assert result.raw_stdout == "[]"

    def test_json_scalar_stdout_is_failure(self):
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout='"just a string"')):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"


# ---------------------------------------------------------------------------
# Result text extraction
# ---------------------------------------------------------------------------


class TestResultText:
    def test_success_extracts_result_text(self):
        envelope = (FIXTURES / "claude_implementer_success.json").read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())
        assert result.result_text.startswith("I've implemented")

    def test_error_extracts_result_text(self):
        envelope = (FIXTURES / "claude_implementer_error.json").read_text()
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())
        assert "permission issues" in result.result_text

    def test_missing_result_field_gives_empty_string(self):
        envelope = json.dumps({"is_error": False})
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=envelope)):
            result = adapter.execute(_request())
        assert result.result_text == ""

    def test_nonzero_exit_still_extracts_result_text(self):
        envelope = json.dumps({"result": "partial output", "is_error": False})
        adapter = ClaudeCodeAdapter()
        with patch(
            "subprocess.run", side_effect=_mock_run(stdout=envelope, returncode=1)
        ):
            result = adapter.execute(_request())
        assert result.result_text == "partial output"

    def test_unparseable_stdout_gives_empty_result_text(self):
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout="not json")):
            result = adapter.execute(_request())
        assert result.result_text == ""

    def test_timeout_gives_empty_result_text(self):
        adapter = ClaudeCodeAdapter()
        with patch("subprocess.run", side_effect=_mock_run(timeout=True)):
            result = adapter.execute(_request())
        assert result.result_text == ""


# ---------------------------------------------------------------------------
# Working directory
# ---------------------------------------------------------------------------


class TestWorkingDirectory:
    def test_cwd_passed_to_subprocess(self):
        adapter = ClaudeCodeAdapter()
        with patch(
            "subprocess.run", side_effect=_mock_run(stdout='{"is_error":false}')
        ) as mock:
            adapter.execute(_request(working_directory="/my/repo"))
        _, kwargs = mock.call_args
        assert kwargs["cwd"] == "/my/repo"
