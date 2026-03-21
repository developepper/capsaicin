"""Tests for run-cost and denial logging (T03).

Covers:
- build_run_end_payload helper with cost and denials
- log denial entries using T01 normalized shape
- activity.log integration for implementer runs
- activity.log integration for reviewer runs
- permission-denied runs log denials with file_path and command
- cost is included when adapter reports it
- clean runs without denials do not include denial fields
- reviewer contract_violation is logged as final classified outcome
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from capsaicin.activity_log import build_run_end_payload, _log_denials
from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import (
    CriterionChecked,
    ReviewResult,
    RunRequest,
    RunResult,
    ScopeReviewed,
)
from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.ticket_add import _get_project_id, add_ticket_inline
from capsaicin.ticket_review import run_review_pipeline
from capsaicin.ticket_run import run_implementation_pipeline


# ---------------------------------------------------------------------------
# Unit tests: build_run_end_payload
# ---------------------------------------------------------------------------


class TestBuildRunEndPayload:
    def test_basic_payload(self):
        p = build_run_end_payload("success", 1.5)
        assert p == {"exit_status": "success", "duration": 1.5}

    def test_includes_cost(self):
        meta = {"total_cost_usd": 0.0342}
        p = build_run_end_payload("success", 2.0, meta)
        assert p["total_cost_usd"] == 0.0342

    def test_no_cost_when_absent(self):
        meta = {"session_id": "abc"}
        p = build_run_end_payload("success", 1.0, meta)
        assert "total_cost_usd" not in p

    def test_includes_denial_count_and_details(self):
        meta = {
            "normalized_denials": [
                {"tool_name": "Edit", "tool_use_id": "t1", "file_path": "/a.py"},
                {"tool_name": "Bash", "tool_use_id": "t2", "command": "rm -rf /"},
            ]
        }
        p = build_run_end_payload("permission_denied", 3.0, meta)
        assert p["denial_count"] == 2
        assert len(p["denials"]) == 2
        # Uses T01 normalized shape: tool_name, file_path, command
        assert p["denials"][0] == {"tool_name": "Edit", "file_path": "/a.py"}
        assert p["denials"][1] == {"tool_name": "Bash", "command": "rm -rf /"}

    def test_cost_and_denials_together(self):
        meta = {
            "total_cost_usd": 0.85,
            "normalized_denials": [
                {"tool_name": "Edit", "tool_use_id": "t1", "file_path": "/b.py"},
            ],
        }
        p = build_run_end_payload("permission_denied", 200.0, meta)
        assert p["total_cost_usd"] == 0.85
        assert p["denial_count"] == 1
        assert p["denials"][0]["file_path"] == "/b.py"

    def test_no_denials_when_empty(self):
        meta = {"normalized_denials": []}
        p = build_run_end_payload("success", 1.0, meta)
        assert "denial_count" not in p
        assert "denials" not in p

    def test_none_metadata(self):
        p = build_run_end_payload("failure", 0.5, None)
        assert p == {"exit_status": "failure", "duration": 0.5}

    def test_raw_denials_fallback_count_only(self):
        """When normalized_denials is absent, fall back to raw for count."""
        meta = {
            "permission_denials": [
                {"tool_name": "Write", "tool_use_id": "x"},
            ]
        }
        p = build_run_end_payload("permission_denied", 1.0, meta)
        assert p["denial_count"] == 1
        assert "denials" not in p  # No details without normalized

    def test_raw_string_denials_ignored(self):
        """String-format denials like ['Write'] should not produce a count."""
        meta = {"permission_denials": ["Write"]}
        p = build_run_end_payload("failure", 1.0, meta)
        assert "denial_count" not in p


class TestLogDenials:
    def test_edit_with_file_path(self):
        normalized = [{"tool_name": "Edit", "tool_use_id": "t1", "file_path": "/a.py"}]
        result = _log_denials(normalized)
        assert result == [{"tool_name": "Edit", "file_path": "/a.py"}]

    def test_bash_with_command(self):
        normalized = [{"tool_name": "Bash", "tool_use_id": "t2", "command": "ls"}]
        result = _log_denials(normalized)
        assert result == [{"tool_name": "Bash", "command": "ls"}]

    def test_unknown_tool(self):
        normalized = [{"tool_name": "Read", "tool_use_id": "t3"}]
        result = _log_denials(normalized)
        assert result == [{"tool_name": "Read"}]

    def test_tool_use_id_omitted(self):
        """tool_use_id should be stripped for compactness."""
        normalized = [{"tool_name": "Edit", "tool_use_id": "t1", "file_path": "/x.py"}]
        result = _log_denials(normalized)
        assert "tool_use_id" not in result[0]


# ---------------------------------------------------------------------------
# Test adapters
# ---------------------------------------------------------------------------


class PermissionDeniedAdapter(BaseAdapter):
    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="permission_denied",
            duration_seconds=200.0,
            result_text="Permission denied.",
            raw_stdout="{}",
            raw_stderr="",
            adapter_metadata={
                "total_cost_usd": 0.85,
                "permission_denials": [
                    {"tool_name": "Edit", "tool_use_id": "t1",
                     "tool_input": {"file_path": "/app/main.py"}},
                ],
                "normalized_denials": [
                    {"tool_name": "Edit", "tool_use_id": "t1",
                     "file_path": "/app/main.py"},
                ],
            },
        )


class SuccessAdapter(BaseAdapter):
    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=45.0,
            raw_stdout="{}",
            raw_stderr="",
            adapter_metadata={"total_cost_usd": 0.034},
        )


class DiffProducingAdapter(BaseAdapter):
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
            adapter_metadata={"total_cost_usd": 0.05},
        )


class MockReviewAdapter(BaseAdapter):
    """Adapter that returns a pre-configured ReviewResult."""

    def __init__(self, verdict="pass", confidence="high", exit_status="success"):
        self.verdict = verdict
        self.confidence = confidence
        self._exit_status = exit_status
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)

        if self._exit_status != "success":
            return RunResult(
                run_id=request.run_id,
                exit_status=self._exit_status,
                duration_seconds=1.0,
                raw_stdout="",
                raw_stderr="error",
                adapter_metadata={"total_cost_usd": 0.02},
            )

        criteria_checked = [
            CriterionChecked(criterion_id=c.id, description=c.description)
            for c in request.acceptance_criteria
        ]
        sr = ScopeReviewed(
            files_examined=["impl.txt"],
            tests_run=False,
            criteria_checked=criteria_checked,
        )
        review_result = ReviewResult(
            verdict=self.verdict,
            confidence=self.confidence,
            findings=[],
            scope_reviewed=sr,
        )

        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=30.0,
            raw_stdout="review done",
            raw_stderr="",
            structured_result=review_result,
            adapter_metadata={"total_cost_usd": 0.12},
        )


class FileModifyingReviewAdapter(BaseAdapter):
    """Adapter that modifies tracked files (triggers contract violation)."""

    def __init__(self, repo_path):
        self.repo_path = repo_path
        self.calls: list[RunRequest] = []
        self._call_count = 0

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        self._call_count += 1
        (self.repo_path / "impl.txt").write_text(
            f"reviewer was here {self._call_count}\n"
        )
        review_result = ReviewResult(
            verdict="pass",
            confidence="high",
            findings=[],
            scope_reviewed=ScopeReviewed(files_examined=["impl.txt"]),
        )
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=15.0,
            raw_stdout="done",
            raw_stderr="",
            structured_result=review_result,
            adapter_metadata={"total_cost_usd": 0.07},
        )


class PermissionDeniedReviewAdapter(BaseAdapter):
    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="permission_denied",
            duration_seconds=10.0,
            result_text="Permission denied on read.",
            raw_stdout="{}",
            raw_stderr="",
            adapter_metadata={
                "total_cost_usd": 0.03,
                "normalized_denials": [
                    {"tool_name": "Bash", "tool_use_id": "t1", "command": "ls"},
                ],
            },
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "impl.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True,
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


def _add_ticket(env):
    return add_ticket_inline(
        env["conn"], env["project_id"], "Test", "Do it", [], env["log_path"]
    )


def _get_ticket(conn, tid):
    return dict(conn.execute(
        "SELECT id, project_id, title, description, status, "
        "current_cycle, current_impl_attempt, current_review_attempt "
        "FROM tickets WHERE id = ?", (tid,),
    ).fetchone())


def _parse_run_end_payloads(log_path: Path) -> list[dict]:
    """Extract all RUN_END JSON payloads from the activity log."""
    payloads = []
    for line in log_path.read_text().splitlines():
        if "RUN_END" not in line:
            continue
        json_str = line.rsplit(" ", 1)[-1]
        payloads.append(json.loads(json_str))
    return payloads


def _run_impl_to_in_review(env, ticket_id=None):
    """Run implementation pipeline to get a ticket into in-review status."""
    if ticket_id is None:
        ticket_id = _add_ticket(env)
    ticket = _get_ticket(env["conn"], ticket_id)
    adapter = DiffProducingAdapter(env["repo"])
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


# ---------------------------------------------------------------------------
# Implementer integration
# ---------------------------------------------------------------------------


class TestActivityLogImplPermissionDenied:
    def test_run_end_includes_cost_and_denials(self, project_env):
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

        payloads = _parse_run_end_payloads(env["log_path"])
        assert len(payloads) == 1
        p = payloads[0]
        assert p["exit_status"] == "permission_denied"
        assert p["total_cost_usd"] == 0.85
        assert p["denial_count"] == 1
        assert p["denials"][0]["tool_name"] == "Edit"
        assert p["denials"][0]["file_path"] == "/app/main.py"

    def test_permission_denied_not_logged_as_success(self, project_env):
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

        payloads = _parse_run_end_payloads(env["log_path"])
        for p in payloads:
            assert p["exit_status"] != "success"


class TestActivityLogImplSuccessWithCost:
    def test_run_end_includes_cost_no_denials(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        adapter = SuccessAdapter()

        run_implementation_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=adapter,
            log_path=env["log_path"],
        )

        payloads = _parse_run_end_payloads(env["log_path"])
        assert len(payloads) == 1
        p = payloads[0]
        assert p["exit_status"] == "success"
        assert p["total_cost_usd"] == 0.034
        assert "denial_count" not in p
        assert "denials" not in p


# ---------------------------------------------------------------------------
# Reviewer integration
# ---------------------------------------------------------------------------


class TestActivityLogReviewerPass:
    def test_reviewer_run_end_includes_cost(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        review_adapter = MockReviewAdapter(verdict="pass", confidence="high")

        run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=review_adapter,
            log_path=env["log_path"],
        )

        payloads = _parse_run_end_payloads(env["log_path"])
        # Should have impl RUN_END + reviewer RUN_END
        reviewer_payloads = [p for p in payloads if p.get("total_cost_usd") == 0.12]
        assert len(reviewer_payloads) == 1
        p = reviewer_payloads[0]
        assert p["exit_status"] == "success"
        assert "denial_count" not in p


class TestActivityLogReviewerPermissionDenied:
    def test_reviewer_permission_denied_logged(self, project_env):
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        review_adapter = PermissionDeniedReviewAdapter()

        run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=review_adapter,
            log_path=env["log_path"],
        )

        payloads = _parse_run_end_payloads(env["log_path"])
        reviewer_payloads = [p for p in payloads if p["exit_status"] == "permission_denied"]
        assert len(reviewer_payloads) == 1
        p = reviewer_payloads[0]
        assert p["total_cost_usd"] == 0.03
        assert p["denial_count"] == 1
        assert p["denials"][0]["tool_name"] == "Bash"
        assert p["denials"][0]["command"] == "ls"


class TestActivityLogReviewerContractViolation:
    def test_contract_violation_logged_as_final_status(self, project_env):
        """RUN_END should reflect the final classified outcome, not the
        adapter's initial exit_status of 'success'."""
        env = project_env
        tid = _run_impl_to_in_review(env)
        ticket = _get_ticket(env["conn"], tid)
        # This adapter modifies tracked files, triggering contract_violation
        review_adapter = FileModifyingReviewAdapter(env["repo"])

        run_review_pipeline(
            conn=env["conn"],
            project_id=env["project_id"],
            ticket=ticket,
            config=env["config"],
            adapter=review_adapter,
            log_path=env["log_path"],
        )

        payloads = _parse_run_end_payloads(env["log_path"])
        # Filter to reviewer RUN_END payloads (impl payload has cost 0.05)
        reviewer_payloads = [p for p in payloads if p.get("total_cost_usd") == 0.07]
        assert len(reviewer_payloads) >= 1
        # All reviewer RUN_ENDs should show contract_violation, not success
        for p in reviewer_payloads:
            assert p["exit_status"] == "contract_violation"
