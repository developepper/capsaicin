"""Tests for the automated loop (T27)."""

from __future__ import annotations

import subprocess

import pytest

from capsaicin.adapters.types import (
    CriterionChecked,
    Finding,
    ReviewResult,
    RunResult,
    ScopeReviewed,
)
from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.loop import run_loop
from capsaicin.orchestrator import get_state
from capsaicin.ticket_add import _get_project_id, add_ticket_inline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockAdapter:
    """Adapter that returns predetermined results in sequence."""

    def __init__(self, results=None):
        self.calls = []
        self._results = list(results or [])
        self._index = 0

    def execute(self, request):
        self.calls.append(request)
        if self._index < len(self._results):
            result = self._results[self._index]
            self._index += 1
            return self._patch_criteria_checked(result, request)
        # Default: success with no structured result (implementer)
        return RunResult(
            run_id="mock",
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="ok",
            raw_stderr="",
        )

    @staticmethod
    def _patch_criteria_checked(result, request):
        """Auto-populate criteria_checked from the request for reviewer results.

        This ensures mock review results pass T17 defense-in-depth
        validation at the orchestrator layer (confidence:high requires
        criteria_checked when acceptance criteria are provided).
        """
        if (
            result.structured_result
            and request.acceptance_criteria
            and not result.structured_result.scope_reviewed.criteria_checked
        ):
            import dataclasses

            new_scope = dataclasses.replace(
                result.structured_result.scope_reviewed,
                criteria_checked=[
                    CriterionChecked(criterion_id=c.id, description=c.description)
                    for c in request.acceptance_criteria
                ],
            )
            new_review = dataclasses.replace(
                result.structured_result,
                scope_reviewed=new_scope,
            )
            return dataclasses.replace(result, structured_result=new_review)
        return result


def _make_pass_review_result():
    return RunResult(
        run_id="mock",
        exit_status="success",
        duration_seconds=1.0,
        raw_stdout="ok",
        raw_stderr="",
        structured_result=ReviewResult(
            verdict="pass",
            confidence="high",
            findings=[],
            scope_reviewed=ScopeReviewed(files_examined=["impl.txt"]),
        ),
    )


def _make_fail_review_result():
    return RunResult(
        run_id="mock",
        exit_status="success",
        duration_seconds=1.0,
        raw_stdout="ok",
        raw_stderr="",
        structured_result=ReviewResult(
            verdict="fail",
            confidence="high",
            findings=[
                Finding(
                    severity="blocking",
                    category="correctness",
                    description="Missing test coverage",
                    location="impl.txt",
                )
            ],
            scope_reviewed=ScopeReviewed(files_examined=["impl.txt"]),
        ),
    )


def _make_impl_success():
    return RunResult(
        run_id="mock",
        exit_status="success",
        duration_seconds=1.0,
        raw_stdout="ok",
        raw_stderr="",
    )


def _make_impl_failure():
    return RunResult(
        run_id="mock",
        exit_status="failure",
        duration_seconds=1.0,
        raw_stdout="",
        raw_stderr="error",
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


def _add_ticket(env, title="Test ticket"):
    return add_ticket_inline(
        env["conn"],
        env["project_id"],
        title,
        "Do something",
        ["criterion 1"],
        env["log_path"],
    )


def _get_ticket_status(conn, ticket_id):
    return conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()["status"]


def _make_workspace_change(env):
    """Create a tracked file change so the diff is non-empty."""
    (env["repo"] / "impl.txt").write_text("changed\n")


# ---------------------------------------------------------------------------
# Basic loop: run -> review pass -> human-gate
# ---------------------------------------------------------------------------


class TestLoopRunReviewPass:
    def test_stops_at_human_gate_on_pass(self, project_env):
        env = project_env
        tid = _add_ticket(env, title="Loop Test")
        _make_workspace_change(env)

        # Sequence: impl success, then review pass
        adapter = MockAdapter(
            results=[_make_impl_success(), _make_pass_review_result()]
        )
        final_status, detail = run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "Awaiting human decision" in detail
        assert "review_passed" in detail
        assert _get_ticket_status(env["conn"], tid) == "human-gate"
        assert len(adapter.calls) == 2

    def test_never_auto_approves(self, project_env):
        """Even with a clean pass, ticket must stop at human-gate."""
        env = project_env
        tid = _add_ticket(env)
        _make_workspace_change(env)

        adapter = MockAdapter(
            results=[_make_impl_success(), _make_pass_review_result()]
        )
        final_status, _ = run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            log_path=env["log_path"],
        )

        # Must not be pr-ready or done
        assert final_status == "human-gate"
        ticket_status = _get_ticket_status(env["conn"], tid)
        assert ticket_status not in ("pr-ready", "done")


# ---------------------------------------------------------------------------
# Loop with revise cycle: run -> review fail -> run -> review pass
# ---------------------------------------------------------------------------


class TestLoopRevise:
    def test_loops_on_fail_then_passes(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        _make_workspace_change(env)

        # Sequence: impl, review fail, impl (revise), review pass
        adapter = MockAdapter(
            results=[
                _make_impl_success(),
                _make_fail_review_result(),
                _make_impl_success(),
                _make_pass_review_result(),
            ]
        )
        final_status, detail = run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert len(adapter.calls) == 4
        assert _get_ticket_status(env["conn"], tid) == "human-gate"


# ---------------------------------------------------------------------------
# Cycle limit
# ---------------------------------------------------------------------------


class TestLoopCycleLimit:
    def test_stops_at_cycle_limit(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        _make_workspace_change(env)

        # max_cycles=1: after first review fail, the revise->run path
        # detects cycle limit and goes to human-gate
        adapter = MockAdapter(
            results=[
                _make_impl_success(),
                _make_fail_review_result(),
            ]
        )
        final_status, detail = run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            max_cycles=1,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "cycle_limit" in detail

    def test_max_cycles_override(self, project_env):
        """--max-cycles overrides config default."""
        env = project_env
        tid = _add_ticket(env)
        _make_workspace_change(env)

        # Default max_cycles is 3, override to 2
        # Sequence: cycle 1 (impl + fail review), cycle 2 (impl tries but hits limit)
        adapter = MockAdapter(
            results=[
                _make_impl_success(),
                _make_fail_review_result(),
                _make_impl_success(),
                _make_fail_review_result(),
            ]
        )
        final_status, _ = run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            max_cycles=2,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"


# ---------------------------------------------------------------------------
# Blocked on adapter failure
# ---------------------------------------------------------------------------


class TestLoopBlocked:
    def test_blocks_on_repeated_impl_failure(self, project_env):
        env = project_env
        tid = _add_ticket(env)

        # All impl runs fail — should hit retry limit and block
        adapter = MockAdapter(
            results=[_make_impl_failure(), _make_impl_failure(), _make_impl_failure()]
        )
        final_status, detail = run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert final_status == "blocked"
        assert "blocked" in detail.lower()
        assert _get_ticket_status(env["conn"], tid) == "blocked"

    def test_stops_on_empty_implementation(self, project_env):
        """Impl with no changes -> human-gate with empty_implementation."""
        env = project_env
        tid = _add_ticket(env)
        # Don't change any files — empty diff

        adapter = MockAdapter(results=[_make_impl_success()])
        final_status, detail = run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "empty_implementation" in detail


# ---------------------------------------------------------------------------
# Auto-select ticket
# ---------------------------------------------------------------------------


class TestLoopAutoSelect:
    def test_auto_selects_ready_ticket(self, project_env):
        env = project_env
        tid = _add_ticket(env, title="Auto Select")
        _make_workspace_change(env)

        adapter = MockAdapter(
            results=[_make_impl_success(), _make_pass_review_result()]
        )
        final_status, detail = run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert _get_ticket_status(env["conn"], tid) == "human-gate"

    def test_no_eligible_ticket(self, project_env):
        env = project_env
        adapter = MockAdapter()
        with pytest.raises(ValueError, match="No eligible ticket"):
            run_loop(
                env["conn"],
                env["project_id"],
                env["config"],
                adapter,
                adapter,
                log_path=env["log_path"],
            )


# ---------------------------------------------------------------------------
# DB state consistency
# ---------------------------------------------------------------------------


class TestLoopDbState:
    def test_orchestrator_state_consistent(self, project_env):
        """After loop stops, orchestrator state should match."""
        env = project_env
        tid = _add_ticket(env)
        _make_workspace_change(env)

        adapter = MockAdapter(
            results=[_make_impl_success(), _make_pass_review_result()]
        )
        run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"

    def test_state_transitions_recorded(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        _make_workspace_change(env)

        adapter = MockAdapter(
            results=[_make_impl_success(), _make_pass_review_result()]
        )
        run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
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
        # Should include: null->ready, ready->implementing,
        # implementing->in-review, in-review->human-gate
        assert ("ready", "implementing") in statuses
        assert ("implementing", "in-review") in statuses
        assert ("in-review", "human-gate") in statuses

    def test_agent_runs_recorded(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        _make_workspace_change(env)

        adapter = MockAdapter(
            results=[_make_impl_success(), _make_pass_review_result()]
        )
        run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            log_path=env["log_path"],
        )

        runs = (
            env["conn"]
            .execute(
                "SELECT role, exit_status FROM agent_runs "
                "WHERE ticket_id = ? ORDER BY started_at",
                (tid,),
            )
            .fetchall()
        )
        roles = [r["role"] for r in runs]
        assert "implementer" in roles
        assert "reviewer" in roles

    def test_run_diffs_persisted(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        _make_workspace_change(env)

        adapter = MockAdapter(
            results=[_make_impl_success(), _make_pass_review_result()]
        )
        run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            ticket_id=tid,
            log_path=env["log_path"],
        )

        diffs = (
            env["conn"]
            .execute(
                "SELECT rd.diff_text FROM run_diffs rd "
                "JOIN agent_runs ar ON ar.id = rd.run_id "
                "WHERE ar.ticket_id = ?",
                (tid,),
            )
            .fetchall()
        )
        assert len(diffs) >= 1
        assert diffs[0]["diff_text"] != ""


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestLoopActivityLog:
    def test_loop_events_logged(self, project_env):
        env = project_env
        _add_ticket(env)
        _make_workspace_change(env)

        adapter = MockAdapter(
            results=[_make_impl_success(), _make_pass_review_result()]
        )
        run_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "LOOP_START" in log_content
        assert "LOOP_STOP" in log_content
