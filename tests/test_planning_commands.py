"""Tests for planning commands, queries, and CLI surface (T03)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from capsaicin.app.commands import PlanningCommandResult
from capsaicin.app.commands.approve_epic import approve
from capsaicin.app.commands.defer_epic import defer
from capsaicin.app.commands.draft_epic import draft
from capsaicin.app.commands.new_epic import new_epic
from capsaicin.app.commands.review_epic import review
from capsaicin.app.commands.revise_epic import revise
from capsaicin.app.queries.planning_detail import get_planning_detail
from capsaicin.app.queries.planning_summary import get_planning_summary
from capsaicin.cli import cli
from capsaicin.db import get_connection, run_migrations
from capsaicin.errors import PlannedEpicNotFoundError
from capsaicin.planning_status import render_planning_detail, render_planning_summary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    run_migrations(c)
    c.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
        ("p1", "test", "/tmp/repo"),
    )
    c.commit()
    yield c
    c.close()


def _set_epic_status(conn, epic_id, status, gate_reason=None, blocked_reason=None):
    conn.execute(
        "UPDATE planned_epics SET status = ?, gate_reason = ?, blocked_reason = ? "
        "WHERE id = ?",
        (status, gate_reason, blocked_reason, epic_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# PlanningCommandResult
# ---------------------------------------------------------------------------


class TestPlanningCommandResult:
    def test_fields(self):
        r = PlanningCommandResult(
            epic_id="E1",
            final_status="approved",
            detail="done",
        )
        assert r.epic_id == "E1"
        assert r.final_status == "approved"
        assert r.detail == "done"

    def test_defaults(self):
        r = PlanningCommandResult(epic_id="E1", final_status="new")
        assert r.detail is None
        assert r.gate_reason is None
        assert r.blocked_reason is None


# ---------------------------------------------------------------------------
# new_epic command
# ---------------------------------------------------------------------------


class TestNewEpicCommand:
    def test_creates_epic(self, conn):
        result = new_epic(conn, "p1", "Fix the build system")
        assert result.final_status == "new"
        assert result.epic_id is not None

        row = conn.execute(
            "SELECT status, problem_statement FROM planned_epics WHERE id = ?",
            (result.epic_id,),
        ).fetchone()
        assert row["status"] == "new"
        assert row["problem_statement"] == "Fix the build system"

    def test_records_state_transition(self, conn):
        result = new_epic(conn, "p1", "problem")
        row = conn.execute(
            "SELECT from_status, to_status, triggered_by "
            "FROM state_transitions WHERE epic_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (result.epic_id,),
        ).fetchone()
        assert row["from_status"] == "null"
        assert row["to_status"] == "new"
        assert row["triggered_by"] == "human"

    def test_logs_activity(self, conn, tmp_path):
        log = tmp_path / "activity.log"
        new_epic(conn, "p1", "problem", log_path=log)
        assert "EPIC_CREATED" in log.read_text()


# ---------------------------------------------------------------------------
# draft command
# ---------------------------------------------------------------------------


class TestDraftCommand:
    def test_draft_from_new(self, conn):
        r = new_epic(conn, "p1", "problem")
        result = draft(conn, "p1", epic_id=r.epic_id)
        assert result.final_status == "drafting"

    def test_draft_from_revise(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "revise")
        result = draft(conn, "p1", epic_id=r.epic_id)
        assert result.final_status == "drafting"

    def test_draft_wrong_status_raises(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "drafting")
        with pytest.raises(ValueError, match="expected 'new' or 'revise'"):
            draft(conn, "p1", epic_id=r.epic_id)

    def test_auto_select_epic(self, conn):
        r = new_epic(conn, "p1", "problem")
        result = draft(conn, "p1")
        assert result.epic_id == r.epic_id
        assert result.final_status == "drafting"

    def test_auto_select_no_eligible_raises(self, conn):
        with pytest.raises(ValueError, match="No epic eligible for drafting"):
            draft(conn, "p1")


# ---------------------------------------------------------------------------
# review command
# ---------------------------------------------------------------------------


class TestReviewCommand:
    def test_review_from_drafting(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "drafting")
        result = review(conn, "p1", epic_id=r.epic_id)
        assert result.final_status == "in-review"

    def test_review_wrong_status_raises(self, conn):
        r = new_epic(conn, "p1", "problem")
        with pytest.raises(ValueError, match="expected 'drafting'"):
            review(conn, "p1", epic_id=r.epic_id)

    def test_auto_select_epic(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "drafting")
        result = review(conn, "p1")
        assert result.epic_id == r.epic_id

    def test_auto_select_no_eligible_raises(self, conn):
        with pytest.raises(ValueError, match="No epic eligible for review"):
            review(conn, "p1")


# ---------------------------------------------------------------------------
# revise command
# ---------------------------------------------------------------------------


class TestReviseCommand:
    def test_revise_from_human_gate(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        result = revise(conn, "p1", epic_id=r.epic_id)
        assert result.final_status == "revise"

    def test_revise_adds_findings(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        result = revise(
            conn, "p1", epic_id=r.epic_id, add_findings=["fix scope", "add tests"]
        )
        assert result.final_status == "revise"
        assert "2 finding(s)" in result.detail

        findings = conn.execute(
            "SELECT * FROM planning_findings WHERE epic_id = ? AND disposition = 'open'",
            (r.epic_id,),
        ).fetchall()
        assert len(findings) == 2

    def test_revise_wrong_status_raises(self, conn):
        r = new_epic(conn, "p1", "problem")
        with pytest.raises(ValueError, match="expected 'human-gate'"):
            revise(conn, "p1", epic_id=r.epic_id)

    def test_auto_select_epic(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        result = revise(conn, "p1")
        assert result.epic_id == r.epic_id


# ---------------------------------------------------------------------------
# approve command
# ---------------------------------------------------------------------------


class TestApproveCommand:
    def test_approve_from_human_gate(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        result = approve(conn, "p1", epic_id=r.epic_id, rationale="looks good")
        assert result.final_status == "approved"

    def test_approve_records_decision(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        approve(conn, "p1", epic_id=r.epic_id, rationale="lgtm")

        row = conn.execute(
            "SELECT decision, rationale FROM decisions WHERE epic_id = ?",
            (r.epic_id,),
        ).fetchone()
        assert row["decision"] == "approve"
        assert row["rationale"] == "lgtm"

    def test_approve_wrong_status_raises(self, conn):
        r = new_epic(conn, "p1", "problem")
        with pytest.raises(ValueError, match="expected 'human-gate'"):
            approve(conn, "p1", epic_id=r.epic_id)

    def test_auto_select_epic(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        result = approve(conn, "p1")
        assert result.epic_id == r.epic_id


# ---------------------------------------------------------------------------
# defer command
# ---------------------------------------------------------------------------


class TestDeferCommand:
    def test_defer_from_human_gate(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        result = defer(conn, "p1", epic_id=r.epic_id, rationale="not now")
        assert result.final_status == "blocked"

    def test_defer_records_decision(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        defer(conn, "p1", epic_id=r.epic_id, rationale="not now")

        row = conn.execute(
            "SELECT decision, rationale FROM decisions WHERE epic_id = ?",
            (r.epic_id,),
        ).fetchone()
        assert row["decision"] == "defer"
        assert row["rationale"] == "not now"

    def test_defer_sets_blocked_reason(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        defer(conn, "p1", epic_id=r.epic_id, rationale="not now")

        row = conn.execute(
            "SELECT blocked_reason FROM planned_epics WHERE id = ?",
            (r.epic_id,),
        ).fetchone()
        assert row["blocked_reason"] == "not now"

    def test_defer_wrong_status_raises(self, conn):
        r = new_epic(conn, "p1", "problem")
        with pytest.raises(ValueError, match="expected 'human-gate'"):
            defer(conn, "p1", epic_id=r.epic_id)


# ---------------------------------------------------------------------------
# Planning queries
# ---------------------------------------------------------------------------


class TestPlanningSummaryQuery:
    def test_empty_summary(self, conn):
        data = get_planning_summary(conn, "p1")
        assert data.total_epics == 0
        assert data.counts_by_status == {}

    def test_counts_by_status(self, conn):
        new_epic(conn, "p1", "problem 1")
        new_epic(conn, "p1", "problem 2")
        r3 = new_epic(conn, "p1", "problem 3")
        _set_epic_status(conn, r3.epic_id, "drafting")

        data = get_planning_summary(conn, "p1")
        assert data.total_epics == 3
        assert data.counts_by_status["new"] == 2
        assert data.counts_by_status["drafting"] == 1

    def test_active_epics(self, conn):
        r = new_epic(conn, "p1", "problem")
        data = get_planning_summary(conn, "p1")
        assert len(data.active_epics) == 1
        assert data.active_epics[0]["id"] == r.epic_id

    def test_human_gate_epics(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        data = get_planning_summary(conn, "p1")
        assert len(data.human_gate_epics) == 1
        assert data.human_gate_epics[0]["gate_reason"] == "review_passed"

    def test_blocked_epics(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "blocked", blocked_reason="needs input")
        data = get_planning_summary(conn, "p1")
        assert len(data.blocked_epics) == 1
        assert data.blocked_epics[0]["blocked_reason"] == "needs input"


class TestPlanningDetailQuery:
    def test_basic_detail(self, conn):
        r = new_epic(conn, "p1", "problem")
        data = get_planning_detail(conn, r.epic_id)
        assert data.epic["id"] == r.epic_id
        assert data.epic["status"] == "new"
        assert data.planned_tickets == []
        assert data.open_findings == []

    def test_nonexistent_epic_raises(self, conn):
        with pytest.raises(PlannedEpicNotFoundError):
            get_planning_detail(conn, "FAKE")

    def test_verbose_includes_transitions(self, conn):
        r = new_epic(conn, "p1", "problem")
        data = get_planning_detail(conn, r.epic_id, verbose=True)
        assert data.transition_history is not None
        assert len(data.transition_history) >= 1

    def test_nonverbose_no_transitions(self, conn):
        r = new_epic(conn, "p1", "problem")
        data = get_planning_detail(conn, r.epic_id, verbose=False)
        assert data.transition_history is None

    def test_impl_tickets_returned_for_approved_epic(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "approved")
        # Create planned tickets
        conn.execute(
            "INSERT INTO planned_tickets "
            "(id, epic_id, sequence, title, goal, scope, non_goals, references_, implementation_notes) "
            "VALUES (?, ?, ?, ?, ?, '[]', '[]', '[]', '[]')",
            ("pt1", r.epic_id, 1, "Set up DB", "Create schema"),
        )
        conn.execute(
            "INSERT INTO planned_tickets "
            "(id, epic_id, sequence, title, goal, scope, non_goals, references_, implementation_notes) "
            "VALUES (?, ?, ?, ?, ?, '[]', '[]', '[]', '[]')",
            ("pt2", r.epic_id, 2, "Add API", "Build endpoints"),
        )
        # Create impl tickets linked to planned tickets
        conn.execute(
            "INSERT INTO tickets (id, project_id, title, description, status, planned_ticket_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t1", "p1", "Set up DB", "Create schema", "done", "pt1"),
        )
        conn.execute(
            "INSERT INTO tickets (id, project_id, title, description, status, planned_ticket_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t2", "p1", "Add API", "Build endpoints", "ready", "pt2"),
        )
        # t2 depends on t1
        conn.execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            ("t2", "t1"),
        )
        conn.commit()

        data = get_planning_detail(conn, r.epic_id)
        assert len(data.impl_tickets) == 2

        it1 = data.impl_tickets[0]
        assert it1["id"] == "t1"
        assert it1["title"] == "Set up DB"
        assert it1["status"] == "done"
        assert it1["planned_ticket_id"] == "pt1"
        assert it1["sequence"] == 1
        assert it1["dependencies"] == []
        assert it1["is_ready"] is True

        it2 = data.impl_tickets[1]
        assert it2["id"] == "t2"
        assert it2["planned_ticket_id"] == "pt2"
        assert it2["sequence"] == 2
        assert len(it2["dependencies"]) == 1
        assert it2["dependencies"][0]["depends_on_id"] == "t1"
        assert it2["dependencies"][0]["status"] == "done"
        assert it2["is_ready"] is True

    def test_impl_tickets_empty_when_none_materialized(self, conn):
        r = new_epic(conn, "p1", "problem")
        data = get_planning_detail(conn, r.epic_id)
        assert data.impl_tickets == []

    def test_impl_ticket_not_ready_when_dep_not_done(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "approved")
        conn.execute(
            "INSERT INTO planned_tickets "
            "(id, epic_id, sequence, title, goal, scope, non_goals, references_, implementation_notes) "
            "VALUES (?, ?, ?, ?, ?, '[]', '[]', '[]', '[]')",
            ("pt1", r.epic_id, 1, "First", "goal1"),
        )
        conn.execute(
            "INSERT INTO planned_tickets "
            "(id, epic_id, sequence, title, goal, scope, non_goals, references_, implementation_notes) "
            "VALUES (?, ?, ?, ?, ?, '[]', '[]', '[]', '[]')",
            ("pt2", r.epic_id, 2, "Second", "goal2"),
        )
        conn.execute(
            "INSERT INTO tickets (id, project_id, title, description, status, planned_ticket_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t1", "p1", "First", "goal1", "implementing", "pt1"),
        )
        conn.execute(
            "INSERT INTO tickets (id, project_id, title, description, status, planned_ticket_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t2", "p1", "Second", "goal2", "ready", "pt2"),
        )
        conn.execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES (?, ?)",
            ("t2", "t1"),
        )
        conn.commit()

        data = get_planning_detail(conn, r.epic_id)
        it2 = data.impl_tickets[1]
        assert it2["is_ready"] is False
        assert it2["dependencies"][0]["status"] == "implementing"


# ---------------------------------------------------------------------------
# Planning status rendering
# ---------------------------------------------------------------------------


class TestPlanningStatusRendering:
    def test_render_empty_summary(self, conn):
        output = render_planning_summary(conn, "p1")
        assert "Planning Summary (0 epics)" in output
        assert "No planned epics" in output

    def test_render_summary_with_epics(self, conn):
        new_epic(conn, "p1", "problem 1")
        r2 = new_epic(conn, "p1", "problem 2")
        _set_epic_status(conn, r2.epic_id, "drafting")

        output = render_planning_summary(conn, "p1")
        assert "Planning Summary (2 epics)" in output
        assert "new: 1" in output
        assert "drafting: 1" in output
        assert "Active Epics:" in output

    def test_render_detail(self, conn):
        r = new_epic(conn, "p1", "Fix the build system")
        output = render_planning_detail(conn, r.epic_id)
        assert f"Epic: {r.epic_id}" in output
        assert "Status: new" in output
        assert "Fix the build system" in output
        assert "Planned Tickets:" in output
        assert "(none)" in output

    def test_render_detail_verbose(self, conn):
        r = new_epic(conn, "p1", "problem")
        output = render_planning_detail(conn, r.epic_id, verbose=True)
        assert "Transition History:" in output

    def test_render_detail_with_gate_reason(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "human-gate", gate_reason="review_passed")
        output = render_planning_detail(conn, r.epic_id)
        assert "Gate Reason: review_passed" in output

    def test_render_detail_with_blocked_reason(self, conn):
        r = new_epic(conn, "p1", "problem")
        _set_epic_status(conn, r.epic_id, "blocked", blocked_reason="needs input")
        output = render_planning_detail(conn, r.epic_id)
        assert "Blocked Reason: needs input" in output


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestPlanCLI:
    def test_plan_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["plan", "--help"])
        assert result.exit_code == 0
        assert "Manage planning epics" in result.output

    def test_plan_new_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["plan", "new", "--help"])
        assert result.exit_code == 0
        assert "--problem" in result.output

    def test_plan_subcommands_registered(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["plan", "--help"])
        assert result.exit_code == 0
        for cmd in ["new", "draft", "review", "revise", "approve", "defer", "status"]:
            assert cmd in result.output, f"'{cmd}' not found in plan --help output"

    def test_plan_new_requires_problem(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["plan", "new"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()
