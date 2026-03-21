"""Tests for the human revision pipeline (T22)."""

from __future__ import annotations

import pytest

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import ReviewResult, RunRequest, RunResult, ScopeReviewed
from capsaicin.orchestrator import get_state
from capsaicin.ticket_revise import revise_ticket, select_revise_ticket
from capsaicin.ticket_run import run_implementation_pipeline
from capsaicin.ticket_review import run_review_pipeline
from tests.adapters import DiffProducingAdapter
from tests.conftest import add_ticket, get_ticket, get_ticket_status


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


class MockReviewAdapter(BaseAdapter):
    def __init__(self, verdict="pass", confidence="high"):
        self.calls: list[RunRequest] = []
        self.verdict = verdict
        self.confidence = confidence

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            structured_result=ReviewResult(
                verdict=self.verdict,
                confidence=self.confidence,
                scope_reviewed=ScopeReviewed(files_examined=["impl.txt"]),
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_to_human_gate(env):
    """Run impl + review to get a ticket into human-gate."""
    tid = add_ticket(env, criteria=["criterion 1"])
    ticket = get_ticket(env["conn"], tid)

    impl_adapter = DiffProducingAdapter(env["repo"])
    run_implementation_pipeline(
        env["conn"],
        env["project_id"],
        ticket,
        env["config"],
        impl_adapter,
        log_path=env["log_path"],
    )

    ticket = get_ticket(env["conn"], tid)
    review_adapter = MockReviewAdapter(verdict="pass", confidence="high")
    run_review_pipeline(
        env["conn"],
        env["project_id"],
        ticket,
        env["config"],
        review_adapter,
        log_path=env["log_path"],
    )

    assert get_ticket_status(env["conn"], tid) == "human-gate"
    return tid


# ---------------------------------------------------------------------------
# select_revise_ticket
# ---------------------------------------------------------------------------


class TestSelectReviseTicket:
    def test_auto_select_human_gate(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = select_revise_ticket(env["conn"])
        assert ticket["id"] == tid

    def test_explicit_ticket_id(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = select_revise_ticket(env["conn"], tid)
        assert ticket["id"] == tid

    def test_wrong_status(self, project_env):
        env = project_env
        tid = add_ticket(env, criteria=["criterion 1"])
        with pytest.raises(ValueError, match="expected 'human-gate'"):
            select_revise_ticket(env["conn"], tid)

    def test_not_found(self, project_env):
        with pytest.raises(ValueError, match="not found"):
            select_revise_ticket(project_env["conn"], "nonexistent")

    def test_no_human_gate_tickets(self, project_env):
        with pytest.raises(ValueError, match="No ticket found"):
            select_revise_ticket(project_env["conn"])


# ---------------------------------------------------------------------------
# Basic revise (no findings, no reset)
# ---------------------------------------------------------------------------


class TestReviseBasic:
    def test_transitions_to_revise(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        final = revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        assert final == "revise"
        assert get_ticket_status(env["conn"], tid) == "revise"

    def test_orchestrator_idle(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_decision_recorded(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        assert dict(rows[0])["decision"] == "revise"

    def test_state_transition_recorded(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
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
        assert ("human-gate", "revise") in statuses

    def test_counters_preserved_without_reset(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket_before = get_ticket(env["conn"], tid)
        cycle_before = ticket_before["current_cycle"]

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket_before,
            log_path=env["log_path"],
        )

        ticket_after = get_ticket(env["conn"], tid)
        assert ticket_after["current_cycle"] == cycle_before


# ---------------------------------------------------------------------------
# Human findings
# ---------------------------------------------------------------------------


class TestHumanFindings:
    def test_findings_persisted(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            add_findings=["Fix the null check", "Add error handling"],
            log_path=env["log_path"],
        )

        findings = (
            env["conn"]
            .execute(
                "SELECT * FROM findings WHERE ticket_id = ? AND category = 'human_feedback'",
                (tid,),
            )
            .fetchall()
        )
        assert len(findings) == 2
        descs = {dict(f)["description"] for f in findings}
        assert "Fix the null check" in descs
        assert "Add error handling" in descs

    def test_findings_are_blocking(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            add_findings=["Fix it"],
            log_path=env["log_path"],
        )

        f = dict(
            env["conn"]
            .execute(
                "SELECT * FROM findings WHERE ticket_id = ? AND category = 'human_feedback'",
                (tid,),
            )
            .fetchone()
        )
        assert f["severity"] == "blocking"
        assert f["disposition"] == "open"

    def test_synthetic_run_created(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            add_findings=["Fix it"],
            log_path=env["log_path"],
        )

        runs = (
            env["conn"]
            .execute(
                "SELECT * FROM agent_runs WHERE ticket_id = ? AND role = 'human'",
                (tid,),
            )
            .fetchall()
        )
        assert len(runs) == 1
        run = dict(runs[0])
        assert run["role"] == "human"
        assert run["mode"] == "read-write"
        assert run["exit_status"] == "success"
        assert run["verdict"] is None
        assert run["prompt"] == "human feedback via ticket revise"
        assert run["run_request"] == "{}"
        assert run["duration_seconds"] == 0.0
        assert run["started_at"] is not None
        assert run["finished_at"] == run["started_at"]
        assert run["cycle_number"] == ticket["current_cycle"]
        assert run["attempt_number"] == 1

    def test_findings_linked_to_synthetic_run(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            add_findings=["Fix it"],
            log_path=env["log_path"],
        )

        run = (
            env["conn"]
            .execute(
                "SELECT id FROM agent_runs WHERE ticket_id = ? AND role = 'human'",
                (tid,),
            )
            .fetchone()
        )

        finding = (
            env["conn"]
            .execute(
                "SELECT run_id FROM findings WHERE ticket_id = ? AND category = 'human_feedback'",
                (tid,),
            )
            .fetchone()
        )

        assert finding["run_id"] == run["id"]

    def test_no_findings_no_synthetic_run(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        runs = (
            env["conn"]
            .execute(
                "SELECT * FROM agent_runs WHERE ticket_id = ? AND role = 'human'",
                (tid,),
            )
            .fetchall()
        )
        assert len(runs) == 0


# ---------------------------------------------------------------------------
# Reset cycles
# ---------------------------------------------------------------------------


class TestResetCycles:
    def test_reset_cycles_resets_counters(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            reset_cycles=True,
            log_path=env["log_path"],
        )

        t = get_ticket(env["conn"], tid)
        assert t["current_cycle"] == 0
        assert t["current_impl_attempt"] == 1
        assert t["current_review_attempt"] == 1

    def test_without_reset_preserves_counters(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket_before = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket_before,
            log_path=env["log_path"],
        )

        ticket_after = get_ticket(env["conn"], tid)
        assert ticket_after["current_cycle"] == ticket_before["current_cycle"]
        assert (
            ticket_after["current_impl_attempt"]
            == ticket_before["current_impl_attempt"]
        )


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_decision_logged(self, project_env):
        env = project_env
        tid = _run_to_human_gate(env)
        ticket = get_ticket(env["conn"], tid)

        revise_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "DECISION" in log_content
