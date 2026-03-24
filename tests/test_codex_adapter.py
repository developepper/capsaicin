"""Tests for the OpenAI Codex CLI adapter (T06)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from capsaicin.adapters.codex import CodexAdapter
from capsaicin.adapters.registry import resolve_adapter
from capsaicin.adapters.types import (
    AcceptanceCriterion,
    PlannerResult,
    PlanningReviewResult,
    RunRequest,
)


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
        "adapter_config": {},
    }
    defaults.update(overrides)
    return RunRequest(**defaults)


def _planner_request(**overrides) -> RunRequest:
    defaults = {
        "run_id": "run-plan-001",
        "role": "planner",
        "mode": "read-write",
        "working_directory": "/tmp",
        "prompt": "Plan the work",
        "timeout_seconds": 60,
        "adapter_config": {
            "structured_output": "planner",
        },
    }
    defaults.update(overrides)
    return RunRequest(**defaults)


def _planning_reviewer_request(**overrides) -> RunRequest:
    defaults = {
        "run_id": "run-prev-001",
        "role": "reviewer",
        "mode": "read-only",
        "working_directory": "/tmp",
        "prompt": "Review the plan",
        "timeout_seconds": 60,
        "adapter_config": {
            "structured_output": "planning_review",
            "valid_sequences": [1, 2],
        },
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


def _jsonl(*events: dict) -> str:
    """Build JSONL stdout from event dicts."""
    return "\n".join(json.dumps(ev) for ev in events)


def _success_events(text: str = "Done.") -> str:
    """Build a minimal successful JSONL event stream."""
    return _jsonl(
        {"type": "thread.started"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": text},
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestCodexRegistry:
    def test_codex_resolves_to_adapter(self):
        cls = resolve_adapter("codex")
        assert cls is CodexAdapter

    def test_build_adapter_instance(self):
        from capsaicin.adapters.registry import build_adapter_from_config
        from capsaicin.config import AdapterConfig

        cfg = AdapterConfig(backend="codex", command="/usr/local/bin/codex")
        adapter = build_adapter_from_config(cfg)
        assert isinstance(adapter, CodexAdapter)
        assert adapter.command == "/usr/local/bin/codex"


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


class TestCommandConstruction:
    def test_builds_correct_command(self):
        adapter = CodexAdapter(command="codex")
        req = _request(prompt="Do the thing")
        cmd = adapter._build_command(req)
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "--json" in cmd
        assert "--ephemeral" in cmd
        assert cmd[-1] == "Do the thing"

    def test_custom_command(self):
        adapter = CodexAdapter(command="/opt/homebrew/bin/codex")
        cmd = adapter._build_command(_request())
        assert cmd[0] == "/opt/homebrew/bin/codex"

    def test_read_only_mode_adds_sandbox(self):
        adapter = CodexAdapter()
        req = _request(mode="read-only")
        cmd = adapter._build_command(req)
        assert "--sandbox" in cmd
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "read-only"

    def test_read_write_mode_no_sandbox(self):
        adapter = CodexAdapter()
        req = _request(mode="read-write")
        cmd = adapter._build_command(req)
        assert "--sandbox" not in cmd

    def test_model_override(self):
        adapter = CodexAdapter()
        req = _request(adapter_config={"model": "gpt-4o"})
        cmd = adapter._build_command(req)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "gpt-4o"

    def test_working_directory_via_cd(self):
        adapter = CodexAdapter()
        req = _request(working_directory="/my/repo")
        cmd = adapter._build_command(req)
        assert "--cd" in cmd
        idx = cmd.index("--cd")
        assert cmd[idx + 1] == "/my/repo"

    def test_schema_path_included(self):
        adapter = CodexAdapter()
        req = _request()
        cmd = adapter._build_command(req, schema_path="/tmp/schema.json")
        assert "--output-schema" in cmd
        idx = cmd.index("--output-schema")
        assert cmd[idx + 1] == "/tmp/schema.json"


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------


class TestJsonlParsing:
    def test_parse_jsonl_events(self):
        stdout = _jsonl(
            {"type": "thread.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}},
        )
        events = CodexAdapter._parse_jsonl_events(stdout)
        assert len(events) == 2
        assert events[0]["type"] == "thread.started"

    def test_parse_jsonl_ignores_bad_lines(self):
        stdout = '{"type": "thread.started"}\nnot json\n{"type": "turn.started"}\n'
        events = CodexAdapter._parse_jsonl_events(stdout)
        assert len(events) == 2

    def test_parse_jsonl_empty(self):
        assert CodexAdapter._parse_jsonl_events("") == []
        assert CodexAdapter._parse_jsonl_events("\n\n") == []

    def test_extract_agent_text(self):
        events = [
            {"type": "thread.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "hello"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "world"},
            },
        ]
        text = CodexAdapter._extract_agent_text(events)
        assert text == "hello\nworld"

    def test_extract_agent_text_ignores_non_message_items(self):
        events = [
            {
                "type": "item.completed",
                "item": {"type": "tool_call", "text": "ignored"},
            },
        ]
        assert CodexAdapter._extract_agent_text(events) == ""

    def test_extract_usage_metadata(self):
        events = [
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        ]
        meta = CodexAdapter._extract_usage_metadata(events)
        assert meta["usage"]["input_tokens"] == 100

    def test_detect_error_event(self):
        events = [
            {"type": "error", "error": {"message": "network failure", "code": "net"}},
        ]
        assert CodexAdapter._detect_error_event(events) == "network failure"

    def test_detect_turn_failed(self):
        events = [
            {
                "type": "turn.failed",
                "error": {
                    "code": "invalid_json_schema",
                    "message": "additionalProperties is required",
                },
            },
        ]
        result = CodexAdapter._detect_turn_failed(events)
        assert "invalid_json_schema" in result
        assert "additionalProperties" in result


# ---------------------------------------------------------------------------
# Execute — success path
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    def test_simple_success(self):
        adapter = CodexAdapter()
        stdout = _success_events("Implementation complete.")
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_request())
        assert result.exit_status == "success"
        assert result.run_id == "run-001"
        assert result.result_text == "Implementation complete."
        assert result.raw_stdout == stdout
        assert result.duration_seconds > 0

    def test_usage_metadata_extracted(self):
        adapter = CodexAdapter()
        stdout = _success_events()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_request())
        assert result.adapter_metadata["usage"]["input_tokens"] == 100

    def test_cwd_passed_to_subprocess(self):
        adapter = CodexAdapter()
        with patch(
            "subprocess.run", side_effect=_mock_run(stdout=_success_events())
        ) as mock:
            adapter.execute(_request(working_directory="/my/repo"))
        _, kwargs = mock.call_args
        assert kwargs["cwd"] == "/my/repo"


# ---------------------------------------------------------------------------
# Execute — failure paths
# ---------------------------------------------------------------------------


class TestExecuteFailure:
    def test_nonzero_exit_is_failure(self):
        adapter = CodexAdapter()
        stdout = _jsonl(
            {"type": "turn.failed", "error": {"code": "runtime", "message": "crash"}},
        )
        with patch(
            "subprocess.run", side_effect=_mock_run(stdout=stdout, returncode=1)
        ):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"
        assert result.adapter_metadata.get("error_detail") == "runtime: crash"

    def test_timeout_returns_timeout(self):
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(timeout=True)):
            result = adapter.execute(_request())
        assert result.exit_status == "timeout"
        assert result.duration_seconds > 0

    def test_timeout_captures_partial_output(self):
        adapter = CodexAdapter()
        with patch(
            "subprocess.run",
            side_effect=_mock_run(timeout=True, stdout="partial", stderr="err"),
        ):
            result = adapter.execute(_request())
        assert result.exit_status == "timeout"
        assert result.raw_stdout == "partial"
        assert result.raw_stderr == "err"

    def test_empty_stdout_is_success_with_empty_text(self):
        """Unlike Claude, empty JSONL is not necessarily a failure — process
        exited 0 with no events. We treat it as success with empty text."""
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout="")):
            result = adapter.execute(_request())
        assert result.exit_status == "success"
        assert result.result_text == ""

    def test_nonzero_exit_with_error_event(self):
        adapter = CodexAdapter()
        stdout = _jsonl(
            {"type": "error", "error": {"message": "auth failed"}},
        )
        with patch(
            "subprocess.run", side_effect=_mock_run(stdout=stdout, returncode=1)
        ):
            result = adapter.execute(_request())
        assert result.exit_status == "failure"


# ---------------------------------------------------------------------------
# Permission denial heuristic
# ---------------------------------------------------------------------------


class TestPermissionDenial:
    def test_operation_not_permitted(self):
        adapter = CodexAdapter()
        stdout = _success_events(
            "I tried to write the file but got: Operation not permitted"
        )
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_request())
        assert result.exit_status == "permission_denied"

    def test_read_only_file_system(self):
        adapter = CodexAdapter()
        stdout = _success_events(
            "The write failed because the filesystem is read-only."
        )
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_request())
        assert result.exit_status == "permission_denied"

    def test_permission_denied_text(self):
        adapter = CodexAdapter()
        stdout = _success_events("Error: Permission denied when writing to /tmp/out")
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_request())
        assert result.exit_status == "permission_denied"

    def test_reviewer_mentioning_permission_denied_not_misclassified(self):
        """A reviewer finding that mentions 'Permission denied' as application
        behavior must not trigger the permission-denial heuristic."""
        review_data = {
            "verdict": "fail",
            "confidence": "high",
            "findings": [
                {
                    "severity": "blocking",
                    "category": "correctness",
                    "description": "Permission denied error when user tries to access /admin",
                    "disposition": "open",
                }
            ],
            "scope_reviewed": {
                "files_examined": ["auth.py"],
                "tests_run": True,
                "criteria_checked": [{"criterion_id": "ac-1", "description": "X"}],
            },
        }
        stdout = _success_events(json.dumps(review_data))
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_reviewer_request())
        assert result.exit_status == "success"
        assert result.structured_result is not None
        assert result.structured_result.verdict == "fail"

    def test_planner_mentioning_read_only_not_misclassified(self):
        """A planner result mentioning read-only filesystem in notes must
        not trigger the permission-denial heuristic."""
        planner_data = {
            "epic": {
                "title": "Epic",
                "summary": "Handle read-only filesystem gracefully",
                "success_outcome": "Outcome",
            },
            "tickets": [
                {
                    "sequence": 1,
                    "title": "Ticket 1",
                    "goal": "Goal",
                    "scope": [],
                    "non_goals": [],
                    "acceptance_criteria": [{"description": "Criterion"}],
                    "dependencies": [],
                    "references": [],
                    "implementation_notes": [],
                }
            ],
            "sequencing_notes": "Operation not permitted errors need handling",
            "open_questions": [],
        }
        stdout = _success_events(json.dumps(planner_data))
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_planner_request())
        assert result.exit_status == "success"
        assert isinstance(result.structured_result, PlannerResult)

    def test_normal_text_not_flagged(self):
        adapter = CodexAdapter()
        stdout = _success_events("I implemented the feature successfully.")
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_request())
        assert result.exit_status == "success"


# ---------------------------------------------------------------------------
# Structured output — reviewer
# ---------------------------------------------------------------------------


class TestReviewerStructuredOutput:
    def test_pass_verdict_extracted(self):
        review_data = {
            "verdict": "pass",
            "confidence": "high",
            "findings": [],
            "scope_reviewed": {
                "files_examined": ["a.py"],
                "tests_run": True,
                "criteria_checked": [{"criterion_id": "ac-1", "description": "X"}],
            },
        }
        stdout = _success_events(json.dumps(review_data))
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_reviewer_request())
        assert result.exit_status == "success"
        assert result.structured_result is not None
        assert result.structured_result.verdict == "pass"

    def test_fail_verdict_with_blocking(self):
        review_data = {
            "verdict": "fail",
            "confidence": "high",
            "findings": [
                {
                    "severity": "blocking",
                    "category": "correctness",
                    "description": "Bug",
                    "disposition": "open",
                }
            ],
            "scope_reviewed": {
                "files_examined": ["a.py"],
                "tests_run": True,
                "criteria_checked": [{"criterion_id": "ac-1", "description": "X"}],
            },
        }
        stdout = _success_events(json.dumps(review_data))
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_reviewer_request())
        assert result.exit_status == "success"
        assert result.structured_result.verdict == "fail"

    def test_invalid_review_is_parse_error(self):
        review_data = {
            "verdict": "fail",
            "confidence": "high",
            "findings": [
                {
                    "severity": "warning",
                    "category": "style",
                    "description": "Nit",
                    "disposition": "open",
                }
            ],
            "scope_reviewed": {
                "files_examined": ["a.py"],
                "tests_run": True,
                "criteria_checked": [{"criterion_id": "ac-1", "description": "X"}],
            },
        }
        stdout = _success_events(json.dumps(review_data))
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_reviewer_request())
        assert result.exit_status == "parse_error"

    def test_unparseable_review_text_is_parse_error(self):
        stdout = _success_events("just plain text, no JSON")
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_reviewer_request())
        assert result.exit_status == "parse_error"

    def test_fenced_json_extraction(self):
        review_data = {
            "verdict": "pass",
            "confidence": "high",
            "findings": [],
            "scope_reviewed": {
                "files_examined": ["a.py"],
                "tests_run": True,
                "criteria_checked": [{"criterion_id": "ac-1", "description": "X"}],
            },
        }
        text = "Here is my review:\n```json\n" + json.dumps(review_data) + "\n```"
        stdout = _success_events(text)
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_reviewer_request())
        assert result.exit_status == "success"
        assert result.structured_result.verdict == "pass"


# ---------------------------------------------------------------------------
# Structured output — planner
# ---------------------------------------------------------------------------


class TestPlannerStructuredOutput:
    def test_planner_result_extracted(self):
        planner_data = {
            "epic": {
                "title": "Epic",
                "summary": "Summary",
                "success_outcome": "Outcome",
            },
            "tickets": [
                {
                    "sequence": 1,
                    "title": "Ticket 1",
                    "goal": "Goal",
                    "scope": ["Scope"],
                    "non_goals": [],
                    "acceptance_criteria": [{"description": "Criterion"}],
                    "dependencies": [],
                    "references": [],
                    "implementation_notes": [],
                }
            ],
            "sequencing_notes": "Do it",
            "open_questions": [],
        }
        stdout = _success_events(json.dumps(planner_data))
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_planner_request())
        assert result.exit_status == "success"
        assert isinstance(result.structured_result, PlannerResult)
        assert result.structured_result.epic.title == "Epic"


# ---------------------------------------------------------------------------
# Structured output — planning reviewer
# ---------------------------------------------------------------------------


class TestPlanningReviewerStructuredOutput:
    def test_planning_review_result_extracted(self):
        review_data = {
            "verdict": "pass",
            "confidence": "high",
            "findings": [],
            "scope_reviewed": {
                "epic_reviewed": True,
                "tickets_reviewed": [1, 2],
                "aspects_checked": ["scope"],
            },
        }
        stdout = _success_events(json.dumps(review_data))
        adapter = CodexAdapter()
        with patch("subprocess.run", side_effect=_mock_run(stdout=stdout)):
            result = adapter.execute(_planning_reviewer_request())
        assert result.exit_status == "success"
        assert isinstance(result.structured_result, PlanningReviewResult)
        assert result.structured_result.verdict == "pass"


# ---------------------------------------------------------------------------
# Schema temp file cleanup
# ---------------------------------------------------------------------------


class TestSchemaCleanup:
    def test_schema_file_cleaned_up_on_success(self, tmp_path):
        """Temp schema file should be deleted after execution."""
        adapter = CodexAdapter()
        stdout = _success_events('{"verdict":"pass"}')

        created_files = []

        original_run = subprocess.run

        def capture_schema(cmd, **kwargs):
            for i, arg in enumerate(cmd):
                if arg == "--output-schema" and i + 1 < len(cmd):
                    created_files.append(cmd[i + 1])
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=stdout, stderr=""
            )

        with patch("subprocess.run", side_effect=capture_schema):
            adapter.execute(_reviewer_request())

        # Schema file should have been created and then cleaned up
        import os

        for f in created_files:
            assert not os.path.exists(f), f"Schema file {f} was not cleaned up"

    def test_schema_file_cleaned_up_on_timeout(self):
        """Temp schema file should be deleted even on timeout."""
        adapter = CodexAdapter()

        created_files = []

        def capture_and_timeout(cmd, **kwargs):
            for i, arg in enumerate(cmd):
                if arg == "--output-schema" and i + 1 < len(cmd):
                    created_files.append(cmd[i + 1])
            exc = subprocess.TimeoutExpired(cmd=cmd, timeout=60)
            exc.stdout = ""
            exc.stderr = ""
            raise exc

        with patch("subprocess.run", side_effect=capture_and_timeout):
            result = adapter.execute(_reviewer_request())

        assert result.exit_status == "timeout"
        import os

        for f in created_files:
            assert not os.path.exists(f), f"Schema file {f} was not cleaned up"
