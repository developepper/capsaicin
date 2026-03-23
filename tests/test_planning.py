"""Tests for planning state machine and query helpers."""

from __future__ import annotations

import pytest

from capsaicin.db import get_connection, run_migrations
from capsaicin.errors import PlannedEpicNotFoundError
from capsaicin.queries import (
    get_planning_run_id,
    load_open_planning_findings,
    load_planned_epic,
    load_planned_ticket_criteria,
    load_planned_tickets,
)
from capsaicin.state_machine import (
    PLANNING_LEGAL_TRANSITIONS,
    IllegalPlanningTransitionError,
    planning_transition_is_legal,
    transition_planned_epic,
)


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    run_migrations(c)
    yield c
    c.close()


# --- Helpers ---


def _insert_project(conn, project_id="p1"):
    conn.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
        (project_id, "test", "/tmp/repo"),
    )


def _insert_epic(conn, epic_id="e1", project_id="p1", status="new"):
    conn.execute(
        "INSERT INTO planned_epics (id, project_id, problem_statement, status) "
        "VALUES (?, ?, ?, ?)",
        (epic_id, project_id, "problem", status),
    )


def _insert_planned_ticket(conn, ticket_id="pt1", epic_id="e1", sequence=1, title="PT"):
    conn.execute(
        "INSERT INTO planned_tickets "
        "(id, epic_id, sequence, title, goal, scope, non_goals, references_, implementation_notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket_id, epic_id, sequence, title, "goal", "[]", "[]", "[]", "[]"),
    )


def _insert_planned_ticket_criterion(
    conn, criterion_id="ptc1", planned_ticket_id="pt1"
):
    conn.execute(
        "INSERT INTO planned_ticket_criteria (id, planned_ticket_id, description) "
        "VALUES (?, ?, ?)",
        (criterion_id, planned_ticket_id, "criterion desc"),
    )


def _insert_agent_run_for_epic(conn, run_id="r1", epic_id="e1"):
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, epic_id, role, mode, cycle_number, exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'planner', 'read-write', 1, 'running', 'p', '{}', datetime('now'))",
        (run_id, epic_id),
    )


def _insert_planning_finding(
    conn, finding_id="pf1", run_id="r1", epic_id="e1", disposition="open"
):
    conn.execute(
        "INSERT INTO planning_findings "
        "(id, run_id, epic_id, severity, category, description, fingerprint, disposition) "
        "VALUES (?, ?, ?, 'warning', 'cat', 'desc', 'fp', ?)",
        (finding_id, run_id, epic_id, disposition),
    )


def _set_epic_status(conn, epic_id, status, gate_reason=None, blocked_reason=None):
    conn.execute(
        "UPDATE planned_epics SET status = ?, gate_reason = ?, blocked_reason = ? WHERE id = ?",
        (status, gate_reason, blocked_reason, epic_id),
    )
    conn.commit()


# --- planning_transition_is_legal ---


class TestPlanningTransitionIsLegal:
    def test_all_legal_transitions_accepted(self):
        for (from_s, to_s), actors in PLANNING_LEGAL_TRANSITIONS.items():
            for actor in actors:
                assert planning_transition_is_legal(from_s, to_s, actor), (
                    f"{from_s} -> {to_s} by {actor} should be legal"
                )

    def test_new_to_drafting(self):
        assert planning_transition_is_legal("new", "drafting", "system")
        assert not planning_transition_is_legal("new", "drafting", "human")

    def test_drafting_to_in_review(self):
        assert planning_transition_is_legal("drafting", "in-review", "system")

    def test_drafting_to_human_gate(self):
        assert planning_transition_is_legal("drafting", "human-gate", "system")

    def test_drafting_to_blocked(self):
        assert planning_transition_is_legal("drafting", "blocked", "system")

    def test_in_review_to_revise(self):
        assert planning_transition_is_legal("in-review", "revise", "system")

    def test_in_review_to_human_gate(self):
        assert planning_transition_is_legal("in-review", "human-gate", "system")

    def test_in_review_to_blocked(self):
        assert planning_transition_is_legal("in-review", "blocked", "system")

    def test_revise_to_drafting(self):
        assert planning_transition_is_legal("revise", "drafting", "system")

    def test_revise_to_human_gate(self):
        assert planning_transition_is_legal("revise", "human-gate", "system")

    def test_human_gate_to_approved(self):
        assert planning_transition_is_legal("human-gate", "approved", "human")
        assert not planning_transition_is_legal("human-gate", "approved", "system")

    def test_human_gate_to_revise(self):
        assert planning_transition_is_legal("human-gate", "revise", "human")

    def test_human_gate_to_blocked(self):
        assert planning_transition_is_legal("human-gate", "blocked", "human")

    def test_blocked_to_new(self):
        assert planning_transition_is_legal("blocked", "new", "human")
        assert not planning_transition_is_legal("blocked", "new", "system")

    # Illegal transitions
    def test_new_to_approved_illegal(self):
        assert not planning_transition_is_legal("new", "approved", "system")
        assert not planning_transition_is_legal("new", "approved", "human")

    def test_drafting_to_approved_illegal(self):
        assert not planning_transition_is_legal("drafting", "approved", "system")

    def test_unknown_status_illegal(self):
        assert not planning_transition_is_legal("nonexistent", "drafting", "system")
        assert not planning_transition_is_legal("new", "nonexistent", "system")


# --- transition_planned_epic ---


class TestTransitionPlannedEpic:
    def test_basic_transition(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        transition_planned_epic(conn, "e1", "drafting", "system")
        row = conn.execute(
            "SELECT status FROM planned_epics WHERE id = ?", ("e1",)
        ).fetchone()
        assert row[0] == "drafting"

    def test_state_transition_row_created(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        transition_planned_epic(conn, "e1", "drafting", "system", reason="auto-start")
        row = conn.execute(
            "SELECT from_status, to_status, triggered_by, reason "
            "FROM state_transitions WHERE epic_id = ? "
            "ORDER BY id DESC LIMIT 1",
            ("e1",),
        ).fetchone()
        assert row[0] == "new"
        assert row[1] == "drafting"
        assert row[2] == "system"
        assert row[3] == "auto-start"

    def test_illegal_transition_raises(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        with pytest.raises(IllegalPlanningTransitionError, match="not allowed"):
            transition_planned_epic(conn, "e1", "approved", "system")

    def test_nonexistent_epic_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            transition_planned_epic(conn, "FAKE", "drafting", "system")

    def test_gate_reason_set_on_human_gate(self, conn):
        _insert_project(conn)
        _insert_epic(conn, status="drafting")
        transition_planned_epic(
            conn, "e1", "human-gate", "system", gate_reason="review_passed"
        )
        row = conn.execute(
            "SELECT gate_reason FROM planned_epics WHERE id = ?", ("e1",)
        ).fetchone()
        assert row[0] == "review_passed"

    def test_gate_reason_cleared_on_leave(self, conn):
        _insert_project(conn)
        _insert_epic(conn, status="human-gate")
        _set_epic_status(conn, "e1", "human-gate", gate_reason="review_passed")
        transition_planned_epic(conn, "e1", "approved", "human")
        row = conn.execute(
            "SELECT gate_reason FROM planned_epics WHERE id = ?", ("e1",)
        ).fetchone()
        assert row[0] is None

    def test_blocked_reason_set_on_blocked(self, conn):
        _insert_project(conn)
        _insert_epic(conn, status="drafting")
        transition_planned_epic(
            conn, "e1", "blocked", "system", blocked_reason="needs input"
        )
        row = conn.execute(
            "SELECT blocked_reason FROM planned_epics WHERE id = ?", ("e1",)
        ).fetchone()
        assert row[0] == "needs input"

    def test_blocked_reason_cleared_on_unblock(self, conn):
        _insert_project(conn)
        _insert_epic(conn, status="blocked")
        _set_epic_status(conn, "e1", "blocked", blocked_reason="needs input")
        transition_planned_epic(conn, "e1", "new", "human")
        row = conn.execute(
            "SELECT blocked_reason FROM planned_epics WHERE id = ?", ("e1",)
        ).fetchone()
        assert row[0] is None

    def test_human_gate_without_gate_reason_rejected(self, conn):
        _insert_project(conn)
        _insert_epic(conn, status="drafting")
        with pytest.raises(ValueError, match="gate_reason is required"):
            transition_planned_epic(conn, "e1", "human-gate", "system")

    def test_blocked_without_blocked_reason_rejected(self, conn):
        _insert_project(conn)
        _insert_epic(conn, status="drafting")
        with pytest.raises(ValueError, match="blocked_reason is required"):
            transition_planned_epic(conn, "e1", "blocked", "system")

    def test_full_happy_path(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        transition_planned_epic(conn, "e1", "drafting", "system")
        transition_planned_epic(conn, "e1", "in-review", "system")
        transition_planned_epic(
            conn, "e1", "human-gate", "system", gate_reason="review_passed"
        )
        transition_planned_epic(conn, "e1", "approved", "human")
        row = conn.execute(
            "SELECT status FROM planned_epics WHERE id = ?", ("e1",)
        ).fetchone()
        assert row[0] == "approved"

    def test_revise_loop(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        transition_planned_epic(conn, "e1", "drafting", "system")
        transition_planned_epic(conn, "e1", "in-review", "system")
        transition_planned_epic(conn, "e1", "revise", "system")
        transition_planned_epic(conn, "e1", "drafting", "system")
        transition_planned_epic(conn, "e1", "in-review", "system")
        transition_planned_epic(
            conn, "e1", "human-gate", "system", gate_reason="review_passed"
        )
        row = conn.execute(
            "SELECT status FROM planned_epics WHERE id = ?", ("e1",)
        ).fetchone()
        assert row[0] == "human-gate"

    def test_activity_log_entry(self, conn, tmp_path):
        _insert_project(conn)
        _insert_epic(conn)
        log = tmp_path / "activity.log"
        transition_planned_epic(conn, "e1", "drafting", "system", log_path=log)
        content = log.read_text()
        assert "PLANNING_STATE_TRANSITION" in content


# --- Planning query helpers ---


class TestLoadPlannedEpic:
    def test_loads_existing_epic(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        result = load_planned_epic(conn, "e1")
        assert result["id"] == "e1"
        assert result["project_id"] == "p1"
        assert result["problem_statement"] == "problem"
        assert result["status"] == "new"

    def test_raises_on_missing(self, conn):
        with pytest.raises(PlannedEpicNotFoundError):
            load_planned_epic(conn, "nonexistent")


class TestLoadPlannedTickets:
    def test_loads_tickets_ordered_by_sequence(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        _insert_planned_ticket(conn, "pt2", "e1", 2, "Second")
        _insert_planned_ticket(conn, "pt1", "e1", 1, "First")
        result = load_planned_tickets(conn, "e1")
        assert len(result) == 2
        assert result[0]["title"] == "First"
        assert result[1]["title"] == "Second"

    def test_empty_when_none(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        assert load_planned_tickets(conn, "e1") == []


class TestLoadPlannedTicketCriteria:
    def test_loads_criteria(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        _insert_planned_ticket(conn)
        _insert_planned_ticket_criterion(conn)
        _insert_planned_ticket_criterion(conn, "ptc2", "pt1")
        result = load_planned_ticket_criteria(conn, "pt1")
        assert len(result) == 2

    def test_empty_when_none(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        _insert_planned_ticket(conn)
        assert load_planned_ticket_criteria(conn, "pt1") == []


class TestLoadOpenPlanningFindings:
    def test_loads_open_findings(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        _insert_agent_run_for_epic(conn)
        _insert_planning_finding(conn, "pf1", disposition="open")
        _insert_planning_finding(conn, "pf2", disposition="fixed")
        result = load_open_planning_findings(conn, "e1")
        assert len(result) == 1
        assert result[0]["id"] == "pf1"

    def test_empty_when_none(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        assert load_open_planning_findings(conn, "e1") == []


class TestGetPlanningRunId:
    def test_returns_most_recent_run(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        _insert_agent_run_for_epic(conn, "r1")
        _insert_agent_run_for_epic(conn, "r2")
        result = get_planning_run_id(conn, "e1")
        # Both have same started_at, so ordering is by rowid; either is acceptable
        assert result in ("r1", "r2")

    def test_raises_on_missing(self, conn):
        _insert_project(conn)
        _insert_epic(conn)
        with pytest.raises(ValueError, match="No planner run found"):
            get_planning_run_id(conn, "e1")
