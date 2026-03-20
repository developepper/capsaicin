"""Tests for the unblock pipeline (T24)."""

from __future__ import annotations

import subprocess

import pytest

from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.orchestrator import get_state
from capsaicin.ticket_add import _get_project_id, add_ticket_inline
from capsaicin.ticket_unblock import select_unblock_ticket, unblock_ticket


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
            "SELECT id, project_id, title, description, status, blocked_reason "
            "FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
    )


def _get_ticket_status(conn, ticket_id):
    return conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()["status"]


def _make_blocked_ticket(env):
    """Create a ticket and manually put it in blocked status."""
    tid = _add_ticket(env)
    env["conn"].execute(
        "UPDATE tickets SET status = 'blocked', blocked_reason = 'test failure', "
        "current_cycle = 2, current_impl_attempt = 2, current_review_attempt = 1 "
        "WHERE id = ?",
        (tid,),
    )
    env["conn"].execute(
        "INSERT INTO state_transitions (ticket_id, from_status, to_status, "
        "triggered_by, reason, created_at) VALUES (?, 'implementing', 'blocked', "
        "'system', 'test', datetime('now'))",
        (tid,),
    )
    env["conn"].commit()
    return tid


# ---------------------------------------------------------------------------
# select_unblock_ticket
# ---------------------------------------------------------------------------


class TestSelectUnblockTicket:
    def test_selects_blocked_ticket(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)
        ticket = select_unblock_ticket(env["conn"], tid)
        assert ticket["id"] == tid
        assert ticket["status"] == "blocked"

    def test_wrong_status(self, project_env):
        env = project_env
        tid = _add_ticket(env)
        with pytest.raises(ValueError, match="expected 'blocked'"):
            select_unblock_ticket(env["conn"], tid)

    def test_not_found(self, project_env):
        with pytest.raises(ValueError, match="not found"):
            select_unblock_ticket(project_env["conn"], "nonexistent")


# ---------------------------------------------------------------------------
# Unblock -> ready
# ---------------------------------------------------------------------------


class TestUnblock:
    def test_transitions_to_ready(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        final = unblock_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        assert final == "ready"
        assert _get_ticket_status(env["conn"], tid) == "ready"

    def test_blocked_reason_cleared(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)
        ticket = _get_ticket(env["conn"], tid)
        assert ticket["blocked_reason"] == "test failure"

        unblock_ticket(
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
        assert row["blocked_reason"] is None

    def test_decision_recorded(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        unblock_ticket(
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
        assert dict(rows[0])["decision"] == "unblock"

    def test_orchestrator_idle(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        unblock_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "idle"

    def test_state_transition_recorded(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        unblock_ticket(
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
        assert ("blocked", "ready") in statuses


# ---------------------------------------------------------------------------
# Reset cycles
# ---------------------------------------------------------------------------


class TestResetCycles:
    def test_reset_cycles_resets_counters(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        unblock_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            reset_cycles=True,
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute(
                "SELECT current_cycle, current_impl_attempt, current_review_attempt "
                "FROM tickets WHERE id = ?",
                (tid,),
            )
            .fetchone()
        )
        assert row["current_cycle"] == 0
        assert row["current_impl_attempt"] == 1
        assert row["current_review_attempt"] == 1

    def test_without_reset_preserves_counters(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)

        unblock_ticket(
            env["conn"],
            env["project_id"],
            _get_ticket(env["conn"], tid),
            log_path=env["log_path"],
        )

        row = (
            env["conn"]
            .execute(
                "SELECT current_cycle, current_impl_attempt FROM tickets WHERE id = ?",
                (tid,),
            )
            .fetchone()
        )
        assert row["current_cycle"] == 2
        assert row["current_impl_attempt"] == 2


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestActivityLog:
    def test_unblock_logged(self, project_env):
        env = project_env
        tid = _make_blocked_ticket(env)
        ticket = _get_ticket(env["conn"], tid)

        unblock_ticket(
            env["conn"],
            env["project_id"],
            ticket,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "TICKET_UNBLOCK" in log_content
