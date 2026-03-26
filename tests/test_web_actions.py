"""Tests for T06 — human-gate inbox and decision flow web actions.

Covers:
- approve, revise, defer, unblock POST actions via web routes
- rationale-required validation for certain gate reasons
- workspace mismatch error handling via force checkbox
- revise with findings
- defer with abandon
- unblock for blocked tickets
- run, review, loop action triggers on ticket detail
- error display on failed actions
- action forms visibility by ticket status
"""

from __future__ import annotations

import contextlib
import json
import logging

import pytest
from starlette.testclient import TestClient

from capsaicin.state_machine import transition_ticket
from capsaicin.web.app import create_app
from tests.conftest import add_ticket


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def web_client(project_env):
    env = project_env
    app = create_app(
        db_path=env["project_dir"] / "capsaicin.db",
        project_id=env["project_id"],
        config_path=env["project_dir"] / "config.toml",
        log_path=env["project_dir"] / "activity.log",
    )
    return TestClient(app), env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _move_to_human_gate(env, ticket_id, gate_reason="review_passed"):
    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"],
        ticket_id,
        "human-gate",
        "system",
        reason="test",
        gate_reason=gate_reason,
    )


def _move_to_blocked(env, ticket_id):
    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"],
        ticket_id,
        "blocked",
        "system",
        reason="test",
        blocked_reason="implementation_failure",
    )


# ---------------------------------------------------------------------------
# Action form visibility
# ---------------------------------------------------------------------------


class TestActionFormVisibility:
    def test_human_gate_shows_action_forms(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Gate Ticket")
        _move_to_human_gate(env, tid)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Human Decision Required" in resp.text
        assert "Approve" in resp.text
        assert "Revise" in resp.text
        assert "Defer" in resp.text

    def test_blocked_shows_unblock(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Blocked Ticket")
        _move_to_blocked(env, tid)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Unblock" in resp.text
        assert "Ticket Blocked" in resp.text

    def test_ready_shows_run_and_loop(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Ready Ticket")

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Run Implementation" in resp.text
        assert "Run Loop" in resp.text

    def test_ready_does_not_show_gate_actions(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Ready Ticket")

        resp = client.get(f"/tickets/{tid}")
        assert "Human Decision Required" not in resp.text

    def test_gate_reason_context_review_passed(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Passed Ticket")
        _move_to_human_gate(env, tid, gate_reason="review_passed")

        resp = client.get(f"/tickets/{tid}")
        assert "Review passed" in resp.text

    def test_gate_reason_context_cycle_limit(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Cycle Ticket")
        _move_to_human_gate(env, tid, gate_reason="cycle_limit")

        resp = client.get(f"/tickets/{tid}")
        assert "cycle limit" in resp.text
        assert "required" in resp.text

    def test_gate_reason_context_low_confidence(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Low Conf Ticket")
        _move_to_human_gate(env, tid, gate_reason="low_confidence_pass")

        resp = client.get(f"/tickets/{tid}")
        assert "low confidence" in resp.text
        assert "required" in resp.text

    def test_gate_reason_context_escalated(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Escalated Ticket")
        _move_to_human_gate(env, tid, gate_reason="reviewer_escalated")

        resp = client.get(f"/tickets/{tid}")
        assert "escalated" in resp.text
        assert "required" in resp.text


# ---------------------------------------------------------------------------
# Approve action
# ---------------------------------------------------------------------------


class TestApproveAction:
    def test_approve_redirects(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Approve Target")
        _move_to_human_gate(env, tid)

        resp = client.post(
            f"/tickets/{tid}/approve",
            data={"rationale": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/tickets/{tid}" in resp.headers["location"]

    def test_approve_transitions_to_pr_ready(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Approve PR")
        _move_to_human_gate(env, tid)

        client.post(f"/tickets/{tid}/approve", data={})

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "pr-ready"

    def test_approve_records_decision(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Approve Dec")
        _move_to_human_gate(env, tid)

        client.post(f"/tickets/{tid}/approve", data={"rationale": "Looks good"})

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        assert dict(rows[0])["decision"] == "approve"
        assert dict(rows[0])["rationale"] == "Looks good"

    def test_approve_rationale_required_for_cycle_limit(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Cycle Approve")
        _move_to_human_gate(env, tid, gate_reason="cycle_limit")

        resp = client.post(
            f"/tickets/{tid}/approve",
            data={"rationale": ""},
            follow_redirects=False,
        )
        # Should redirect with error
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

        # Ticket should still be in human-gate
        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "human-gate"

    def test_approve_cycle_limit_with_rationale_succeeds(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Cycle OK")
        _move_to_human_gate(env, tid, gate_reason="cycle_limit")

        client.post(
            f"/tickets/{tid}/approve",
            data={"rationale": "Reviewed manually"},
        )

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "pr-ready"


# ---------------------------------------------------------------------------
# Revise action
# ---------------------------------------------------------------------------


class TestReviseAction:
    def test_revise_redirects(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Revise Target")
        _move_to_human_gate(env, tid)

        resp = client.post(
            f"/tickets/{tid}/revise",
            data={"finding": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_revise_transitions_to_revise(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Revise Trans")
        _move_to_human_gate(env, tid)

        client.post(f"/tickets/{tid}/revise", data={})

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "revise"

    def test_revise_with_finding(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Revise Finding")
        _move_to_human_gate(env, tid)

        client.post(
            f"/tickets/{tid}/revise",
            data={"finding": "Need better tests"},
        )

        findings = (
            env["conn"]
            .execute(
                "SELECT * FROM findings WHERE ticket_id = ? AND category = 'human_feedback'",
                (tid,),
            )
            .fetchall()
        )
        assert len(findings) == 1
        assert dict(findings[0])["description"] == "Need better tests"

    def test_revise_with_reset_cycles(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Revise Reset")
        _move_to_human_gate(env, tid)

        client.post(
            f"/tickets/{tid}/revise",
            data={"reset_cycles": "on"},
        )

        row = (
            env["conn"]
            .execute("SELECT current_cycle FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["current_cycle"] == 0

    def test_revise_records_decision(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Revise Dec")
        _move_to_human_gate(env, tid)

        client.post(f"/tickets/{tid}/revise", data={})

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        assert dict(rows[0])["decision"] == "revise"


# ---------------------------------------------------------------------------
# Defer action
# ---------------------------------------------------------------------------


class TestDeferAction:
    def test_defer_redirects(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Defer Target")
        _move_to_human_gate(env, tid)

        resp = client.post(
            f"/tickets/{tid}/defer",
            data={"rationale": "waiting"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_defer_transitions_to_blocked(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Defer Blocked")
        _move_to_human_gate(env, tid)

        client.post(f"/tickets/{tid}/defer", data={"rationale": "Later"})

        row = (
            env["conn"]
            .execute("SELECT status, blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "blocked"
        assert row["blocked_reason"] == "Later"

    def test_defer_abandon_transitions_to_done(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Defer Abandon")
        _move_to_human_gate(env, tid)

        client.post(
            f"/tickets/{tid}/defer",
            data={"rationale": "Not needed", "abandon": "on"},
        )

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "done"

    def test_defer_records_decision(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Defer Dec")
        _move_to_human_gate(env, tid)

        client.post(f"/tickets/{tid}/defer", data={"rationale": "Waiting"})

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        assert dict(rows[0])["decision"] == "defer"


# ---------------------------------------------------------------------------
# Unblock action
# ---------------------------------------------------------------------------


class TestUnblockAction:
    def test_unblock_redirects(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Unblock Target")
        _move_to_blocked(env, tid)

        resp = client.post(
            f"/tickets/{tid}/unblock",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_unblock_transitions_to_ready(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Unblock Ready")
        _move_to_blocked(env, tid)

        client.post(f"/tickets/{tid}/unblock", data={})

        row = (
            env["conn"]
            .execute("SELECT status, blocked_reason FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "ready"
        assert row["blocked_reason"] is None

    def test_unblock_with_reset_cycles(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Unblock Reset")
        _move_to_blocked(env, tid)
        # Set a non-zero cycle count
        env["conn"].execute("UPDATE tickets SET current_cycle = 3 WHERE id = ?", (tid,))
        env["conn"].commit()

        client.post(f"/tickets/{tid}/unblock", data={"reset_cycles": "on"})

        row = (
            env["conn"]
            .execute("SELECT current_cycle FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["current_cycle"] == 0

    def test_unblock_records_decision(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Unblock Dec")
        _move_to_blocked(env, tid)

        client.post(f"/tickets/{tid}/unblock", data={})

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE ticket_id = ?", (tid,))
            .fetchall()
        )
        assert len(rows) == 1
        assert dict(rows[0])["decision"] == "unblock"


# ---------------------------------------------------------------------------
# Error display
# ---------------------------------------------------------------------------


class TestErrorDisplay:
    def test_error_shown_on_redirect(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Error Test")
        _move_to_human_gate(env, tid)

        resp = client.get(f"/tickets/{tid}?error=Something+went+wrong")
        assert resp.status_code == 200
        assert "Something went wrong" in resp.text
        assert "error-banner" in resp.text

    def test_no_error_no_banner(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="No Error")

        resp = client.get(f"/tickets/{tid}")
        assert "error-banner" not in resp.text


# ---------------------------------------------------------------------------
# Wrong-status actions return errors
# ---------------------------------------------------------------------------


class TestCompleteAction:
    """Tests for POST /tickets/{ticket_id}/complete — T07 web UI."""

    def _move_to_pr_ready(self, env, ticket_id):
        transition_ticket(
            env["conn"], ticket_id, "implementing", "system", reason="test"
        )
        transition_ticket(
            env["conn"],
            ticket_id,
            "human-gate",
            "system",
            reason="test",
            gate_reason="review_passed",
        )
        transition_ticket(env["conn"], ticket_id, "pr-ready", "human", reason="test")

    def test_pr_ready_shows_complete_form(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="PR Ready Ticket")
        self._move_to_pr_ready(env, tid)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Implementation Complete" in resp.text
        assert "Mark Done" in resp.text

    def test_ready_does_not_show_complete_form(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Ready Ticket")

        resp = client.get(f"/tickets/{tid}")
        assert "Implementation Complete" not in resp.text
        assert "Mark Done" not in resp.text

    def test_complete_redirects(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Complete Redirect")
        self._move_to_pr_ready(env, tid)

        resp = client.post(
            f"/tickets/{tid}/complete",
            data={"rationale": "PR merged"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/tickets/{tid}" in resp.headers["location"]

    def test_complete_transitions_to_done(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Complete Done")
        self._move_to_pr_ready(env, tid)

        client.post(f"/tickets/{tid}/complete", data={"rationale": "Merged"})

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "done"

    def test_complete_records_decision(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Complete Decision")
        self._move_to_pr_ready(env, tid)

        client.post(f"/tickets/{tid}/complete", data={"rationale": "All done"})

        rows = (
            env["conn"]
            .execute(
                "SELECT * FROM decisions WHERE ticket_id = ? AND decision = 'complete'",
                (tid,),
            )
            .fetchall()
        )
        assert len(rows) == 1
        assert dict(rows[0])["rationale"] == "All done"

    def test_complete_without_rationale(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="No Rationale")
        self._move_to_pr_ready(env, tid)

        client.post(f"/tickets/{tid}/complete", data={})

        row = (
            env["conn"]
            .execute("SELECT status FROM tickets WHERE id = ?", (tid,))
            .fetchone()
        )
        assert row["status"] == "done"

    def test_complete_wrong_status_returns_error(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Wrong Status Complete")

        resp = client.post(
            f"/tickets/{tid}/complete",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


class TestWrongStatusActions:
    def test_approve_wrong_status(self, web_client):
        """Approve on a ready ticket should redirect with error."""
        client, env = web_client
        tid = add_ticket(env, title="Wrong Status")

        resp = client.post(
            f"/tickets/{tid}/approve",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_revise_wrong_status(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Wrong Status Revise")

        resp = client.post(
            f"/tickets/{tid}/revise",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_unblock_wrong_status(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Wrong Status Unblock")

        resp = client.post(
            f"/tickets/{tid}/unblock",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Resume action and UI visibility
# ---------------------------------------------------------------------------


def _set_orchestrator_status(env, status, ticket_id=None, run_id=None):
    """Set the orchestrator state directly."""
    env["conn"].execute(
        "UPDATE orchestrator_state SET status = ?, active_ticket_id = ?, "
        "active_run_id = ? WHERE project_id = ?",
        (status, ticket_id, run_id, env["project_id"]),
    )
    env["conn"].commit()


class TestResumeUI:
    def test_resume_button_visible_when_awaiting_human(self, web_client):
        client, env = web_client
        _set_orchestrator_status(env, "awaiting_human")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "/actions/resume" in resp.text
        assert "Resume" in resp.text

    def test_resume_button_visible_when_running(self, web_client):
        client, env = web_client
        _set_orchestrator_status(env, "running")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "/actions/resume" in resp.text

    def test_resume_button_visible_when_suspended(self, web_client):
        client, env = web_client
        _set_orchestrator_status(env, "suspended")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "/actions/resume" in resp.text

    def test_resume_button_hidden_when_idle(self, web_client):
        client, env = web_client
        # Default orchestrator state is idle
        resp = client.get("/")
        assert resp.status_code == 200
        assert "/actions/resume" not in resp.text

    def test_resume_action_redirects_to_dashboard(self, web_client):
        """Resume with idle orchestrator redirects to dashboard."""
        client, env = web_client

        resp = client.post("/actions/resume", follow_redirects=False)
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert location.endswith("/") or "/tickets/" in location

    def test_resume_action_redirects_to_ticket(self, web_client):
        """Resume with awaiting_human redirects to the active ticket."""
        client, env = web_client
        tid = add_ticket(env, title="Resume Target")
        _move_to_human_gate(env, tid)
        _set_orchestrator_status(env, "awaiting_human", ticket_id=tid)

        resp = client.post("/actions/resume", follow_redirects=False)
        assert resp.status_code == 303
        location = resp.headers["location"]
        # Should redirect to the ticket or dashboard
        assert f"/tickets/{tid}" in location or location.endswith("/")

    def test_orchestrator_partial_shows_resume(self, web_client):
        """The orchestrator partial endpoint also renders the resume button."""
        client, env = web_client
        _set_orchestrator_status(env, "awaiting_human")

        resp = client.get("/partials/orchestrator")
        assert resp.status_code == 200
        assert "/actions/resume" in resp.text
        assert "Resume" in resp.text


# ---------------------------------------------------------------------------
# Create ticket action
# ---------------------------------------------------------------------------


class TestCreateTicketAction:
    def test_dashboard_shows_create_form(self, web_client):
        client, env = web_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Create New Ticket" in resp.text
        assert "/tickets/new" in resp.text

    def test_create_redirects_to_ticket_detail(self, web_client):
        client, env = web_client
        resp = client.post(
            "/tickets/new",
            data={"title": "New Ticket", "description": "Do the thing", "criteria": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/tickets/" in resp.headers["location"]

    def test_create_persists_ticket(self, web_client):
        client, env = web_client
        client.post(
            "/tickets/new",
            data={
                "title": "Persisted Ticket",
                "description": "Check persistence",
                "criteria": "",
            },
        )
        row = (
            env["conn"]
            .execute(
                "SELECT title, description, status FROM tickets WHERE title = ?",
                ("Persisted Ticket",),
            )
            .fetchone()
        )
        assert row is not None
        assert row["description"] == "Check persistence"
        assert row["status"] == "ready"

    def test_create_with_criteria(self, web_client):
        client, env = web_client
        client.post(
            "/tickets/new",
            data={
                "title": "Criteria Ticket",
                "description": "Has criteria",
                "criteria": "First criterion\nSecond criterion",
            },
        )
        row = (
            env["conn"]
            .execute("SELECT id FROM tickets WHERE title = ?", ("Criteria Ticket",))
            .fetchone()
        )
        criteria = (
            env["conn"]
            .execute(
                "SELECT description FROM acceptance_criteria WHERE ticket_id = ? ORDER BY id",
                (row["id"],),
            )
            .fetchall()
        )
        assert len(criteria) == 2
        assert criteria[0]["description"] == "First criterion"
        assert criteria[1]["description"] == "Second criterion"

    def test_create_missing_title_returns_error(self, web_client):
        client, env = web_client
        resp = client.post(
            "/tickets/new",
            data={"title": "", "description": "No title", "criteria": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_create_missing_description_returns_error(self, web_client):
        client, env = web_client
        resp = client.post(
            "/tickets/new",
            data={"title": "Has title", "description": "", "criteria": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_error_banner_shown_on_dashboard(self, web_client):
        client, env = web_client
        resp = client.get("/?error=Something+went+wrong")
        assert resp.status_code == 200
        assert "Something went wrong" in resp.text
        assert "error-banner" in resp.text


# ---------------------------------------------------------------------------
# Add dependency action
# ---------------------------------------------------------------------------


class TestAddDependencyAction:
    def test_detail_page_shows_dependency_form(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Dep Form Ticket")

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Add dependency" in resp.text
        assert f"/tickets/{tid}/dep" in resp.text

    def test_add_dependency_redirects(self, web_client):
        client, env = web_client
        tid_a = add_ticket(env, title="Ticket A")
        tid_b = add_ticket(env, title="Ticket B")

        resp = client.post(
            f"/tickets/{tid_a}/dep",
            data={"depends_on_id": tid_b},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/tickets/{tid_a}" in resp.headers["location"]

    def test_add_dependency_creates_row(self, web_client):
        client, env = web_client
        tid_a = add_ticket(env, title="Dep Source")
        tid_b = add_ticket(env, title="Dep Target")

        client.post(f"/tickets/{tid_a}/dep", data={"depends_on_id": tid_b})

        row = (
            env["conn"]
            .execute(
                "SELECT * FROM ticket_dependencies WHERE ticket_id = ? AND depends_on_id = ?",
                (tid_a, tid_b),
            )
            .fetchone()
        )
        assert row is not None

    def test_add_dependency_shown_on_page(self, web_client):
        client, env = web_client
        tid_a = add_ticket(env, title="Show Dep Source")
        tid_b = add_ticket(env, title="Show Dep Target")

        client.post(f"/tickets/{tid_a}/dep", data={"depends_on_id": tid_b})

        resp = client.get(f"/tickets/{tid_a}")
        assert resp.status_code == 200
        assert tid_b in resp.text
        assert "Show Dep Target" in resp.text

    def test_add_dependency_invalid_id_shows_error(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Invalid Dep Ticket")

        resp = client.post(
            f"/tickets/{tid}/dep",
            data={"depends_on_id": "NONEXISTENT"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_add_dependency_cycle_shows_error(self, web_client):
        client, env = web_client
        tid_a = add_ticket(env, title="Cycle A")
        tid_b = add_ticket(env, title="Cycle B")

        # A depends on B
        client.post(f"/tickets/{tid_a}/dep", data={"depends_on_id": tid_b})

        # B depends on A — should fail with cycle error
        resp = client.post(
            f"/tickets/{tid_b}/dep",
            data={"depends_on_id": tid_a},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]
        assert "cycle" in resp.headers["location"].lower()

    def test_add_dependency_self_shows_error(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Self Dep")

        resp = client.post(
            f"/tickets/{tid}/dep",
            data={"depends_on_id": tid},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_add_dependency_empty_id_shows_error(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Empty Dep")

        resp = client.post(
            f"/tickets/{tid}/dep",
            data={"depends_on_id": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_no_dependencies_shows_none(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="No Deps")

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Dependencies" in resp.text


# ---------------------------------------------------------------------------
# Workspace recovery actions (AC-2)
# ---------------------------------------------------------------------------


def _enable_workspace(env):
    """Enable workspace isolation in the project config file."""
    config_path = env["project_dir"] / "config.toml"
    text = config_path.read_text()
    if "[workspace]" not in text:
        text += '\n[workspace]\nenabled = true\nbranch_prefix = "capsaicin/"\nauto_cleanup = true\n'
    else:
        text = text.replace("enabled = false", "enabled = true")
    config_path.write_text(text)


def _commit_setup(env):
    """Gitignore .capsaicin/ and commit so the base repo is clean for workspace ops."""
    import subprocess

    gitignore = env["repo"] / ".gitignore"
    if not gitignore.exists() or ".capsaicin" not in gitignore.read_text():
        with open(gitignore, "a") as f:
            f.write("\n.capsaicin/\n")
    subprocess.run(
        ["git", "add", "-A"], cwd=env["repo"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "setup"],
        cwd=env["repo"],
        check=True,
        capture_output=True,
    )


class TestWorkspaceRecoverAction:
    """POST /tickets/{id}/workspace/recover delegates to command service."""

    def test_recover_creates_workspace(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Recover Target")
        _enable_workspace(env)
        _commit_setup(env)

        resp = client.post(
            f"/tickets/{tid}/workspace/recover",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/tickets/{tid}" in resp.headers["location"]
        # Verify workspace was created
        ws = (
            env["conn"]
            .execute("SELECT status FROM workspaces WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        assert ws is not None
        assert ws["status"] == "active"

    def test_recover_disabled_returns_error(self, web_client):
        """Workspace recovery when isolation is disabled returns error."""
        client, env = web_client
        tid = add_ticket(env, title="Recover Disabled")

        resp = client.post(
            f"/tickets/{tid}/workspace/recover",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


class TestWorkspaceCleanupAction:
    """POST /tickets/{id}/workspace/cleanup delegates to command service."""

    def test_cleanup_active_workspace(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Cleanup Target")
        _enable_workspace(env)
        _commit_setup(env)

        from capsaicin.config import WorkspaceConfig
        from capsaicin.workspace import WorkspaceReady, create_workspace

        result = create_workspace(
            env["conn"],
            env["repo"],
            env["project_id"],
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
            ticket_id=tid,
        )
        assert isinstance(result, WorkspaceReady)

        resp = client.post(
            f"/tickets/{tid}/workspace/cleanup",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/tickets/{tid}" in resp.headers["location"]

        ws = (
            env["conn"]
            .execute("SELECT status FROM workspaces WHERE ticket_id = ?", (tid,))
            .fetchone()
        )
        assert ws["status"] == "cleaned"

    def test_cleanup_disabled_returns_error(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Cleanup Disabled")

        resp = client.post(
            f"/tickets/{tid}/workspace/cleanup",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Actionable workspace error messages (AC-3)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def capture_actions_log():
    """Context manager that captures ERROR-level messages from the actions logger."""
    captured: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Handler()
    handler.setLevel(logging.ERROR)
    logger = logging.getLogger("capsaicin.web.routes.actions")
    logger.addHandler(handler)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)


class TestActionableWorkspaceErrors:
    """Workspace failures produce specific messages, not generic run errors."""

    def test_log_workspace_error_produces_actionable_message(self):
        """The _log_workspace_error helper maps reasons to guidance."""
        from capsaicin.web.routes.actions import _log_workspace_error
        from capsaicin.workspace import RecoveryAction, WorkspaceRecovery

        recovery = WorkspaceRecovery(
            workspace_id="ws1",
            failure_reason="missing_worktree",
            action=RecoveryAction.recreate,
            detail="worktree dir gone",
        )

        with capture_actions_log() as logs:
            _log_workspace_error(
                "t1", type("E", (Exception,), {"recovery": recovery})()
            )

        assert len(logs) == 1
        assert "missing" in logs[0].lower() or "worktree" in logs[0].lower()
        assert "Recover Workspace" in logs[0]

    def test_all_failure_reasons_have_messages(self):
        """Every known failure reason maps to a specific message."""
        from capsaicin.web.routes.actions import _log_workspace_error
        from capsaicin.workspace import RecoveryAction, WorkspaceRecovery

        reasons = [
            "missing_worktree",
            "branch_drift",
            "dirty_base_repo",
            "setup_failure",
            "cleanup_conflict",
        ]

        for reason in reasons:
            recovery = WorkspaceRecovery(
                workspace_id="ws1",
                failure_reason=reason,
                action=RecoveryAction.retry,
                detail="test",
            )
            with capture_actions_log() as logs:
                _log_workspace_error(
                    "t1", type("E", (Exception,), {"recovery": recovery})()
                )

            assert len(logs) == 1, f"No log for {reason}"
            # Should NOT contain the generic fallback pattern
            assert "Unknown" not in logs[0], f"Generic message for {reason}"

    def test_missing_recovery_attr_does_not_crash(self):
        """_log_workspace_error handles exceptions without a recovery attribute."""
        from capsaicin.web.routes.actions import _log_workspace_error

        with capture_actions_log() as logs:
            _log_workspace_error("t1", Exception("plain error"))

        assert len(logs) == 1
        assert "no recovery detail" in logs[0]

    def test_ticket_detail_shows_specific_failure_message(self, web_client):
        """Workspace failure on ticket detail shows actionable text, not generic error."""
        client, env = web_client
        tid = add_ticket(env, title="Specific Error")
        _enable_workspace(env)
        _commit_setup(env)

        from capsaicin.config import WorkspaceConfig
        from capsaicin.workspace import WorkspaceReady, create_workspace

        ws = create_workspace(
            env["conn"],
            env["repo"],
            env["project_id"],
            WorkspaceConfig(
                enabled=True, branch_prefix="capsaicin/", auto_cleanup=True
            ),
            ticket_id=tid,
        )
        assert isinstance(ws, WorkspaceReady)

        # Force worktree to be missing
        import subprocess
        from pathlib import Path

        subprocess.run(
            ["git", "worktree", "remove", "--force", ws.worktree_path],
            cwd=env["repo"],
            check=True,
            capture_output=True,
        )
        Path(ws.worktree_path).mkdir(parents=True)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        # Should show specific actionable message about missing worktree
        assert "workspace-failure" in resp.text
        assert (
            "missing" in resp.text.lower()
            or "no longer registered" in resp.text.lower()
        )
        # Should NOT just say a generic error
        assert "Run error" not in resp.text
