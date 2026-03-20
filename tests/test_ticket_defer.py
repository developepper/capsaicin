"""Tests for the defer/abandon pipeline (T23)."""

from __future__ import annotations

import subprocess

import pytest

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import ReviewResult, RunRequest, RunResult, ScopeReviewed
from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.orchestrator import get_state
from capsaicin.ticket_add import _get_project_id, add_ticket_inline
from capsaicin.ticket_defer import defer_ticket, select_defer_ticket
from capsaicin.ticket_run import run_implementation_pipeline
from capsaicin.ticket_review import run_review_pipeline


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


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
        )


class MockReviewAdapter(BaseAdapter):
    def __init__(self):
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            structured_result=ReviewResult(
                verdict="pass",
                confidence="high",
                scope_reviewed=ScopeReviewed(files_examined=["impl.txt"]),
            ),
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


def _add_ticket(env):
    return add_ticket_inline(
        env["conn"],
        env["project_id"],
        "Test ticket",
        "Do something",
        ["criterion 1"],
        env["log_path"],
    )


def _get_ticket(conn, ticket_id):
    return dict(
        conn.execute(
            "SELECT id, project_id, title, description, status, gate_reason "
            "FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
    )


def _get_ticket_status(conn, ticket_id):
    return conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()["status"]


def _run_to_human_gate(env):
    tid = _add_ticket(env)
    ticket = _get_ticket(env["conn"], tid)

    impl_adapter = DiffProducingAdapter(env["repo"])
    run_implementation_pipeline(
        env["conn"],
        env["project_id"],
        ticket,
        env["config"],
        impl_adapter,
        log_path=env["log_path"],
    )

    ticket = _get_ticket(env["conn"], tid)
    review_adapter = MockReviewAdapter()
    run_review_pipeline(
        env["conn"],
        env["project_id"],
        ticket,
        env["config"],
        review_adapter,
        log_path=env["log_path"],
    )

    assert _get_ticket_status(env["conn"], tid) == "human-gate"
    return tid


# ---------------------------------------------------------------------------
# select_defer_ticket
# ---------------------------------------------------------------------------


class TestSelectDeferTicket:
    def test_auto_select(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = select_defer_ticket(env["conn"])
        assert ticket["id"] == tid

    def test_explicit_id(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = select_defer_ticket(env["conn"], tid)
        assert ticket["id"] == tid

    def test_wrong_status(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        with pytest.raises(ValueError, match="expected 'human-gate'"):
            select_defer_ticket(env["conn"], tid)

    def test_not_found(self, project_env):
        with pytest.raises(ValueError, match="not found"):
            select_defer_ticket(project_env["conn"], "nonexistent")

    def test_no_human_gate_tickets(self, project_env):
        with pytest.raises(ValueError, match="No ticket found"):
            select_defer_ticket(project_env["conn"])


# ---------------------------------------------------------------------------
# Defer (no abandon) -> blocked
# ---------------------------------------------------------------------------


class TestDefer:
    def test_transitions_to_blocked(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        final = defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        assert final == "blocked"
        assert _get_ticket_status(env["conn"], tid) == "blocked"

    def test_blocked_reason_set(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            rationale="Need more info",
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["blocked_reason"] == "Need more info"

    def test_default_blocked_reason(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute("SELECT blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["blocked_reason"] == "Deferred by human."

    def test_decision_recorded_as_defer(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            rationale="Later",
            log_path=env["log_path"],
        )

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        d = dict(rows[0])
        assert d["decision"] == "defer"
        assert d["rationale"] == "Later"

    def test_orchestrator_idle(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_state_transition_recorded(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
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
        assert ("human-gate", "blocked") in statuses


# ---------------------------------------------------------------------------
# Abandon -> done
# ---------------------------------------------------------------------------


class TestAbandon:
    def test_transitions_to_done(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        final = defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            abandon=True,
            log_path=env["log_path"],
        )

        assert final == "done"
        assert _get_ticket_status(env["conn"], tid) == "done"

    def test_decision_recorded_as_reject(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            abandon=True,
            rationale="Not needed",
            log_path=env["log_path"],
        )

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        d = dict(rows[0])
        assert d["decision"] == "reject"
        assert d["rationale"] == "Not needed"

    def test_orchestrator_idle(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            abandon=True,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_state_transitions_recorded(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            abandon=True,
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
        assert ("human-gate", "blocked") in statuses
        assert ("blocked", "done") in statuses


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_decision_logged(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = _get_ticket(env["conn"], tid)

        defer_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "DECISION" in log_content
