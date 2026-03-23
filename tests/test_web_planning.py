"""Tests for T06 — planning UI surfaces.

Covers:
- planning dashboard route and rendering
- epic detail route and rendering
- planning action forms visibility by epic status
- approve, revise, defer, unblock POST actions for epics
- planning HTMX partial endpoints
- error display on failed actions
- navigation between planning and implementation dashboards
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from capsaicin.app.commands.new_epic import new_epic
from capsaicin.state_machine import transition_planned_epic
from capsaicin.web.app import create_app


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


def _create_epic(env, problem="Build the widget") -> str:
    """Create a new planned epic and return its ID."""
    result = new_epic(
        conn=env["conn"],
        project_id=env["project_id"],
        problem_statement=problem,
        log_path=env["log_path"],
    )
    return result.epic_id


def _move_epic_to_human_gate(env, epic_id, gate_reason="review_passed"):
    """Transition an epic through to human-gate status."""
    transition_planned_epic(env["conn"], epic_id, "drafting", "system", reason="test")
    transition_planned_epic(
        env["conn"],
        epic_id,
        "human-gate",
        "system",
        reason="test",
        gate_reason=gate_reason,
    )


def _move_epic_to_blocked(env, epic_id):
    """Transition an epic to blocked status."""
    transition_planned_epic(env["conn"], epic_id, "drafting", "system", reason="test")
    transition_planned_epic(
        env["conn"],
        epic_id,
        "blocked",
        "system",
        reason="test",
        blocked_reason="needs_rework",
    )


def _move_epic_to_approved(env, epic_id):
    """Transition an epic through to approved status."""
    _move_epic_to_human_gate(env, epic_id)
    transition_planned_epic(
        env["conn"], epic_id, "approved", "human", reason="approved"
    )


# ---------------------------------------------------------------------------
# Planning dashboard
# ---------------------------------------------------------------------------


class TestPlanningDashboard:
    def test_planning_dashboard_renders(self, web_client):
        client, env = web_client
        resp = client.get("/planning")
        assert resp.status_code == 200
        assert "Planning Dashboard" in resp.text

    def test_planning_dashboard_shows_empty_state(self, web_client):
        client, env = web_client
        resp = client.get("/planning")
        assert "No epics awaiting human action" in resp.text
        assert "No active epics" in resp.text

    def test_planning_dashboard_shows_active_epics(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Active epic problem")

        resp = client.get("/planning")
        assert resp.status_code == 200
        assert "Active epic problem" in resp.text
        assert "Active Epics" in resp.text

    def test_planning_dashboard_shows_gate_epics(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Gate epic problem")
        _move_epic_to_human_gate(env, eid)

        resp = client.get("/planning")
        assert "Planning Inbox" in resp.text
        assert "Gate epic problem" in resp.text

    def test_planning_dashboard_shows_blocked_epics(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Blocked epic problem")
        _move_epic_to_blocked(env, eid)

        resp = client.get("/planning")
        assert "Blocked" in resp.text
        assert "needs_rework" in resp.text

    def test_planning_dashboard_shows_queue_counts(self, web_client):
        client, env = web_client
        _create_epic(env, "Epic one")
        _create_epic(env, "Epic two")

        resp = client.get("/planning")
        assert "Planning Queue" in resp.text
        assert "2 epics" in resp.text

    def test_planning_dashboard_links_to_implementation(self, web_client):
        client, env = web_client
        resp = client.get("/planning")
        assert "Back to implementation dashboard" in resp.text

    def test_implementation_dashboard_links_to_planning(self, web_client):
        client, env = web_client
        resp = client.get("/")
        assert "Planning Dashboard" in resp.text
        assert "/planning" in resp.text


# ---------------------------------------------------------------------------
# Epic detail
# ---------------------------------------------------------------------------


class TestEpicDetail:
    def test_epic_detail_renders(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Detail epic problem")

        resp = client.get(f"/epics/{eid}")
        assert resp.status_code == 200
        assert "Detail epic problem" in resp.text

    def test_epic_detail_shows_status_badge(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Badge test")

        resp = client.get(f"/epics/{eid}")
        assert "planning-status-new" in resp.text

    def test_epic_detail_404_for_missing(self, web_client):
        client, env = web_client
        resp = client.get("/epics/nonexistent-id")
        assert resp.status_code == 404

    def test_epic_detail_shows_error_banner(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Error test")

        resp = client.get(f"/epics/{eid}?error=Something+went+wrong")
        assert "Something went wrong" in resp.text
        assert "error-banner" in resp.text

    def test_epic_detail_no_error_no_banner(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "No error test")

        resp = client.get(f"/epics/{eid}")
        assert "error-banner" not in resp.text

    def test_epic_detail_shows_transition_history(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "History test")

        resp = client.get(f"/epics/{eid}")
        assert "Transition History" in resp.text
        assert "null" in resp.text  # from_status of the initial transition

    def test_epic_detail_back_link(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Back link test")

        resp = client.get(f"/epics/{eid}")
        assert "Back to planning dashboard" in resp.text
        assert "/planning" in resp.text


# ---------------------------------------------------------------------------
# Action form visibility
# ---------------------------------------------------------------------------


class TestPlanningActionFormVisibility:
    def test_human_gate_shows_action_forms(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Gate forms test")
        _move_epic_to_human_gate(env, eid)

        resp = client.get(f"/epics/{eid}")
        assert "Human Decision Required" in resp.text
        assert "Approve" in resp.text
        assert "Revise" in resp.text
        assert "Defer" in resp.text

    def test_new_epic_shows_draft_and_loop(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "New forms test")

        resp = client.get(f"/epics/{eid}")
        assert "Run Draft" in resp.text
        assert "Run Plan Loop" in resp.text

    def test_blocked_epic_shows_unblock(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Blocked forms test")
        _move_epic_to_blocked(env, eid)

        resp = client.get(f"/epics/{eid}")
        assert "Epic Blocked" in resp.text
        assert "Unblock" in resp.text

    def test_approved_epic_shows_materialize(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Approved forms test")
        _move_epic_to_approved(env, eid)

        resp = client.get(f"/epics/{eid}")
        assert "Plan Approved" in resp.text
        assert "Re-materialize" in resp.text

    def test_new_epic_does_not_show_gate_actions(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "No gate test")

        resp = client.get(f"/epics/{eid}")
        assert "Human Decision Required" not in resp.text

    def test_gate_reason_context_review_passed(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Review passed test")
        _move_epic_to_human_gate(env, eid, gate_reason="review_passed")

        resp = client.get(f"/epics/{eid}")
        assert "Plan review passed" in resp.text

    def test_gate_reason_context_cycle_limit(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Cycle limit test")
        _move_epic_to_human_gate(env, eid, gate_reason="cycle_limit")

        resp = client.get(f"/epics/{eid}")
        assert "cycle limit" in resp.text

    def test_drafting_shows_review_action(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Drafting review test")
        transition_planned_epic(env["conn"], eid, "drafting", "system", reason="test")

        resp = client.get(f"/epics/{eid}")
        assert "Run Review" in resp.text


# ---------------------------------------------------------------------------
# Approve epic action
# ---------------------------------------------------------------------------


class TestApproveEpicAction:
    def test_approve_redirects(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Approve redirect")
        _move_epic_to_human_gate(env, eid)

        resp = client.post(
            f"/epics/{eid}/approve",
            data={"rationale": "Good plan"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/epics/{eid}" in resp.headers["location"]

    def test_approve_transitions_to_approved(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Approve transition")
        _move_epic_to_human_gate(env, eid)

        client.post(f"/epics/{eid}/approve", data={"rationale": "LGTM"})

        row = (
            env["conn"]
            .execute("SELECT status FROM planned_epics WHERE id = ?", (eid,))
            .fetchone()
        )
        assert row["status"] == "approved"

    def test_approve_records_decision(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Approve decision")
        _move_epic_to_human_gate(env, eid)

        client.post(f"/epics/{eid}/approve", data={"rationale": "Looks good"})

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE epic_id = ?", (eid,))
            .fetchall()
        )
        assert len(rows) == 1
        assert dict(rows[0])["decision"] == "approve"
        assert dict(rows[0])["rationale"] == "Looks good"

    def test_approve_wrong_status_returns_error(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Wrong status approve")

        resp = client.post(
            f"/epics/{eid}/approve",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Revise epic action
# ---------------------------------------------------------------------------


class TestReviseEpicAction:
    def test_revise_redirects(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Revise redirect")
        _move_epic_to_human_gate(env, eid)

        resp = client.post(
            f"/epics/{eid}/revise",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_revise_transitions_to_revise(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Revise transition")
        _move_epic_to_human_gate(env, eid)

        client.post(f"/epics/{eid}/revise", data={})

        row = (
            env["conn"]
            .execute("SELECT status FROM planned_epics WHERE id = ?", (eid,))
            .fetchone()
        )
        assert row["status"] == "revise"

    def test_revise_with_finding(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Revise finding")
        _move_epic_to_human_gate(env, eid)

        client.post(
            f"/epics/{eid}/revise",
            data={"finding": "Needs better scope definition"},
        )

        findings = (
            env["conn"]
            .execute(
                "SELECT * FROM planning_findings WHERE epic_id = ? "
                "AND category = 'human'",
                (eid,),
            )
            .fetchall()
        )
        assert len(findings) == 1
        assert "scope" in dict(findings[0])["description"].lower()

    def test_revise_wrong_status_returns_error(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Wrong status revise")

        resp = client.post(
            f"/epics/{eid}/revise",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Defer epic action
# ---------------------------------------------------------------------------


class TestDeferEpicAction:
    def test_defer_redirects(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Defer redirect")
        _move_epic_to_human_gate(env, eid)

        resp = client.post(
            f"/epics/{eid}/defer",
            data={"rationale": "waiting"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_defer_transitions_to_blocked(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Defer blocked")
        _move_epic_to_human_gate(env, eid)

        client.post(f"/epics/{eid}/defer", data={"rationale": "Later"})

        row = (
            env["conn"]
            .execute(
                "SELECT status, blocked_reason FROM planned_epics WHERE id = ?",
                (eid,),
            )
            .fetchone()
        )
        assert row["status"] == "blocked"
        assert row["blocked_reason"] == "Later"

    def test_defer_records_decision(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Defer decision")
        _move_epic_to_human_gate(env, eid)

        client.post(f"/epics/{eid}/defer", data={"rationale": "Waiting"})

        rows = (
            env["conn"]
            .execute("SELECT * FROM decisions WHERE epic_id = ?", (eid,))
            .fetchall()
        )
        assert len(rows) == 1
        assert dict(rows[0])["decision"] == "defer"


# ---------------------------------------------------------------------------
# Unblock epic action
# ---------------------------------------------------------------------------


class TestUnblockEpicAction:
    def test_unblock_redirects(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Unblock redirect")
        _move_epic_to_blocked(env, eid)

        resp = client.post(
            f"/epics/{eid}/unblock",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_unblock_transitions_to_new(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Unblock new")
        _move_epic_to_blocked(env, eid)

        client.post(f"/epics/{eid}/unblock", data={})

        row = (
            env["conn"]
            .execute("SELECT status FROM planned_epics WHERE id = ?", (eid,))
            .fetchone()
        )
        assert row["status"] == "new"

    def test_unblock_wrong_status_returns_error(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Wrong status unblock")

        resp = client.post(
            f"/epics/{eid}/unblock",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Planning partials
# ---------------------------------------------------------------------------


class TestPlanningPartials:
    def test_planning_gate_partial(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Gate partial")
        _move_epic_to_human_gate(env, eid)

        resp = client.get("/partials/planning/gate")
        assert resp.status_code == 200
        assert "Gate partial" in resp.text

    def test_planning_active_partial(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Active partial")

        resp = client.get("/partials/planning/active")
        assert resp.status_code == 200
        assert "Active partial" in resp.text

    def test_planning_blocked_partial(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Blocked partial")
        _move_epic_to_blocked(env, eid)

        resp = client.get("/partials/planning/blocked")
        assert resp.status_code == 200
        assert "Blocked partial" in resp.text

    def test_planning_queue_partial(self, web_client):
        client, env = web_client
        _create_epic(env, "Queue partial")

        resp = client.get("/partials/planning/queue")
        assert resp.status_code == 200
        assert "1 epic" in resp.text

    def test_epic_content_partial(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Content partial")

        resp = client.get(f"/partials/epics/{eid}")
        assert resp.status_code == 200
        assert "Content partial" in resp.text

    def test_epic_content_partial_404(self, web_client):
        client, env = web_client
        resp = client.get("/partials/epics/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Workflow trigger actions (background) — verify redirect only
# ---------------------------------------------------------------------------


class TestPlanningWorkflowTriggers:
    def test_draft_redirects(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Draft trigger")

        resp = client.post(
            f"/epics/{eid}/draft",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/epics/{eid}" in resp.headers["location"]

    def test_review_redirects(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Review trigger")

        resp = client.post(
            f"/epics/{eid}/review",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/epics/{eid}" in resp.headers["location"]

    def test_plan_loop_redirects(self, web_client):
        client, env = web_client
        eid = _create_epic(env, "Loop trigger")

        resp = client.post(
            f"/epics/{eid}/loop",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/epics/{eid}" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Planning vs implementation distinction
# ---------------------------------------------------------------------------


class TestPlanningImplementationDistinction:
    def test_planning_badges_use_planning_css_class(self, web_client):
        """Planning status badges use planning-status-* classes, not status-* classes."""
        client, env = web_client
        eid = _create_epic(env, "CSS class test")

        resp = client.get(f"/epics/{eid}")
        assert "planning-status-new" in resp.text

    def test_planning_action_panels_have_planning_class(self, web_client):
        """Planning action panels are visually distinct from ticket panels."""
        client, env = web_client
        eid = _create_epic(env, "Panel class test")
        _move_epic_to_human_gate(env, eid)

        resp = client.get(f"/epics/{eid}")
        assert "planning-action-panel" in resp.text
