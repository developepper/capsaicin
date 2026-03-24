"""Tests for the adapter resolution service (resolver.py).

Covers:
- Precedence chain: ticket override → epic override → project config → fallback
- Role-scope enforcement: epic overrides only for planner/planning_reviewer,
  ticket overrides only for implementer/reviewer
- resolve_all_roles returns source labels
- DB-level CHECK constraints on role_overrides table
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from capsaicin.config import (
    AdapterConfig,
    Config,
    LimitsConfig,
    PathsConfig,
    ProjectConfig,
    ReviewerConfig,
    TicketSelectionConfig,
)
from capsaicin.db import get_connection, run_migrations
from capsaicin.queries import generate_id
from capsaicin.resolver import (
    ResolvedAdapter,
    resolve_adapter_config,
    resolve_all_roles,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    implementer: AdapterConfig | None = None,
    reviewer: AdapterConfig | None = None,
    planner: AdapterConfig | None = None,
    planning_reviewer: AdapterConfig | None = None,
) -> Config:
    """Build a minimal Config for tests."""
    return Config(
        project=ProjectConfig(name="test", repo_path="/tmp/repo"),
        implementer=implementer
        or AdapterConfig(backend="claude-code", command="claude-impl"),
        reviewer=reviewer or AdapterConfig(backend="claude-code", command="claude-rev"),
        limits=LimitsConfig(),
        reviewer_policy=ReviewerConfig(),
        ticket_selection=TicketSelectionConfig(),
        paths=PathsConfig(),
        planner=planner,
        planning_reviewer=planning_reviewer,
    )


def _setup_db() -> sqlite3.Connection:
    """Create an in-memory DB with all migrations applied."""
    conn = get_connection(":memory:")
    run_migrations(conn)
    return conn


def _insert_project(conn: sqlite3.Connection) -> str:
    pid = generate_id()
    conn.execute(
        "INSERT INTO projects (id, name, repo_path, config, created_at) "
        "VALUES (?, 'test', '/tmp/repo', '{}', datetime('now'))",
        (pid,),
    )
    conn.commit()
    return pid


def _insert_epic(conn: sqlite3.Connection, project_id: str) -> str:
    eid = generate_id()
    conn.execute(
        "INSERT INTO planned_epics (id, project_id, problem_statement, status, "
        "created_at, updated_at, status_changed_at) "
        "VALUES (?, ?, 'test problem', 'new', datetime('now'), datetime('now'), datetime('now'))",
        (eid, project_id),
    )
    conn.commit()
    return eid


def _insert_ticket(conn: sqlite3.Connection, project_id: str) -> str:
    tid = generate_id()
    conn.execute(
        "INSERT INTO tickets (id, project_id, title, description, status, "
        "created_at, updated_at, status_changed_at) "
        "VALUES (?, ?, 'test ticket', 'do stuff', 'ready', "
        "datetime('now'), datetime('now'), datetime('now'))",
        (tid, project_id),
    )
    conn.commit()
    return tid


def _insert_override(
    conn: sqlite3.Connection,
    project_id: str,
    role: str,
    backend: str,
    command: str,
    epic_id: str | None = None,
    ticket_id: str | None = None,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
) -> str:
    oid = generate_id()
    conn.execute(
        "INSERT INTO role_overrides "
        "(id, project_id, epic_id, ticket_id, role, backend, command, model, allowed_tools) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            oid,
            project_id,
            epic_id,
            ticket_id,
            role,
            backend,
            command,
            model,
            json.dumps(allowed_tools) if allowed_tools else None,
        ),
    )
    conn.commit()
    return oid


# ---------------------------------------------------------------------------
# Tests: Precedence chain
# ---------------------------------------------------------------------------


class TestResolveAdapterConfig:
    """Test resolve_adapter_config precedence."""

    def test_fallback_when_no_config(self):
        """With a config that has None planner, and no DB, falls back correctly."""
        config = _make_config()
        # planner is None, resolved_planner falls back to implementer
        result = resolve_adapter_config(config, role="planner")
        # Should get the resolved_planner (= implementer since planner is None)
        assert result.command == "claude-impl"

    def test_project_config_for_implementer(self):
        """Without overrides, returns project config."""
        config = _make_config()
        result = resolve_adapter_config(config, role="implementer")
        assert result.backend == "claude-code"
        assert result.command == "claude-impl"

    def test_project_config_for_reviewer(self):
        config = _make_config()
        result = resolve_adapter_config(config, role="reviewer")
        assert result.command == "claude-rev"

    def test_ticket_override_takes_precedence(self):
        """Ticket override wins over project config for implementer."""
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)
        config = _make_config()

        _insert_override(
            conn,
            pid,
            "implementer",
            "custom-backend",
            "custom-cmd",
            ticket_id=tid,
        )

        result = resolve_adapter_config(
            config,
            role="implementer",
            conn=conn,
            ticket_id=tid,
        )
        assert result.backend == "custom-backend"
        assert result.command == "custom-cmd"
        conn.close()

    def test_epic_override_takes_precedence_for_planner(self):
        """Epic override wins over project config for planner."""
        conn = _setup_db()
        pid = _insert_project(conn)
        eid = _insert_epic(conn, pid)
        config = _make_config(
            planner=AdapterConfig(backend="claude-code", command="claude-plan"),
        )

        _insert_override(
            conn,
            pid,
            "planner",
            "custom-plan",
            "plan-override",
            epic_id=eid,
        )

        result = resolve_adapter_config(
            config,
            role="planner",
            conn=conn,
            epic_id=eid,
        )
        assert result.backend == "custom-plan"
        assert result.command == "plan-override"
        conn.close()

    def test_implementer_override_does_not_affect_reviewer(self):
        """AC4: ticket has implementer override but not reviewer — reviewer
        falls back to project config."""
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)
        config = _make_config()

        _insert_override(
            conn,
            pid,
            "implementer",
            "custom-backend",
            "custom-cmd",
            ticket_id=tid,
        )

        # Implementer resolves to override
        impl = resolve_adapter_config(
            config,
            role="implementer",
            conn=conn,
            ticket_id=tid,
        )
        assert impl.backend == "custom-backend"

        # Reviewer resolves to project config
        rev = resolve_adapter_config(
            config,
            role="reviewer",
            conn=conn,
            ticket_id=tid,
        )
        assert rev.command == "claude-rev"
        conn.close()

    def test_no_conn_falls_through_to_config(self):
        """When conn is None, overrides are skipped."""
        config = _make_config()
        result = resolve_adapter_config(config, role="implementer")
        assert result.command == "claude-impl"

    def test_override_with_model_and_allowed_tools(self):
        """Override preserves model and allowed_tools."""
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)
        config = _make_config()

        _insert_override(
            conn,
            pid,
            "reviewer",
            "custom",
            "cmd",
            ticket_id=tid,
            model="sonnet",
            allowed_tools=["Read", "Grep"],
        )

        result = resolve_adapter_config(
            config,
            role="reviewer",
            conn=conn,
            ticket_id=tid,
        )
        assert result.model == "sonnet"
        assert result.allowed_tools == ["Read", "Grep"]
        conn.close()

    def test_invalid_role_raises(self):
        config = _make_config()
        with pytest.raises(ValueError, match="Invalid role"):
            resolve_adapter_config(config, role="invalid")


# ---------------------------------------------------------------------------
# Tests: resolve_all_roles
# ---------------------------------------------------------------------------


class TestResolveAllRoles:
    """Test resolve_all_roles returns config + source labels."""

    def test_all_from_project_config(self):
        config = _make_config(
            planner=AdapterConfig(backend="claude-code", command="planner-cmd"),
            planning_reviewer=AdapterConfig(
                backend="claude-code", command="planrev-cmd"
            ),
        )
        result = resolve_all_roles(config)

        assert set(result.keys()) == {
            "implementer",
            "reviewer",
            "planner",
            "planning_reviewer",
        }
        assert result["implementer"].source == "project config"
        assert result["reviewer"].source == "project config"
        assert result["planner"].source == "project config"
        assert result["planning_reviewer"].source == "project config"
        assert result["implementer"].config.command == "claude-impl"

    def test_fallback_label_for_missing_planner(self):
        """When planner is None in config, resolved_planner is the implementer
        (project config level), not the fallback."""
        config = _make_config()
        result = resolve_all_roles(config)
        # resolved_planner returns implementer, which is project config
        assert result["planner"].source == "project config"

    def test_mixed_sources_with_overrides(self):
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)
        eid = _insert_epic(conn, pid)
        config = _make_config()

        _insert_override(
            conn,
            pid,
            "implementer",
            "override-be",
            "override-cmd",
            ticket_id=tid,
        )
        _insert_override(
            conn,
            pid,
            "planner",
            "plan-be",
            "plan-cmd",
            epic_id=eid,
        )

        result = resolve_all_roles(config, conn=conn, ticket_id=tid, epic_id=eid)
        assert result["implementer"].source == "ticket override"
        assert result["implementer"].config.backend == "override-be"
        assert result["reviewer"].source == "project config"
        assert result["planner"].source == "epic override"
        assert result["planner"].config.command == "plan-cmd"
        assert result["planning_reviewer"].source == "project config"
        conn.close()


# ---------------------------------------------------------------------------
# Tests: Role-scope enforcement (DB constraints)
# ---------------------------------------------------------------------------


class TestRoleScopeConstraints:
    """Verify DB CHECK constraints enforce role-scope rules."""

    def test_epic_override_rejects_implementer(self):
        """Cannot create an epic-level override for implementer role."""
        conn = _setup_db()
        pid = _insert_project(conn)
        eid = _insert_epic(conn, pid)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_override(
                conn,
                pid,
                "implementer",
                "be",
                "cmd",
                epic_id=eid,
            )
        conn.close()

    def test_epic_override_rejects_reviewer(self):
        """Cannot create an epic-level override for reviewer role."""
        conn = _setup_db()
        pid = _insert_project(conn)
        eid = _insert_epic(conn, pid)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_override(
                conn,
                pid,
                "reviewer",
                "be",
                "cmd",
                epic_id=eid,
            )
        conn.close()

    def test_ticket_override_rejects_planner(self):
        """Cannot create a ticket-level override for planner role."""
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_override(
                conn,
                pid,
                "planner",
                "be",
                "cmd",
                ticket_id=tid,
            )
        conn.close()

    def test_ticket_override_rejects_planning_reviewer(self):
        """Cannot create a ticket-level override for planning_reviewer role."""
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_override(
                conn,
                pid,
                "planning_reviewer",
                "be",
                "cmd",
                ticket_id=tid,
            )
        conn.close()

    def test_must_set_exactly_one_scope(self):
        """Cannot create an override with both epic_id and ticket_id."""
        conn = _setup_db()
        pid = _insert_project(conn)
        eid = _insert_epic(conn, pid)
        tid = _insert_ticket(conn, pid)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO role_overrides "
                "(id, project_id, epic_id, ticket_id, role, backend, command) "
                "VALUES (?, ?, ?, ?, 'planner', 'be', 'cmd')",
                (generate_id(), pid, eid, tid),
            )
        conn.close()

    def test_must_set_at_least_one_scope(self):
        """Cannot create an override with neither epic_id nor ticket_id."""
        conn = _setup_db()
        pid = _insert_project(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO role_overrides "
                "(id, project_id, epic_id, ticket_id, role, backend, command) "
                "VALUES (?, ?, NULL, NULL, 'planner', 'be', 'cmd')",
                (generate_id(), pid),
            )
        conn.close()

    def test_epic_override_accepts_planner(self):
        """Epic-level override for planner role succeeds."""
        conn = _setup_db()
        pid = _insert_project(conn)
        eid = _insert_epic(conn, pid)

        _insert_override(conn, pid, "planner", "be", "cmd", epic_id=eid)
        row = conn.execute(
            "SELECT role FROM role_overrides WHERE epic_id = ?", (eid,)
        ).fetchone()
        assert row["role"] == "planner"
        conn.close()

    def test_epic_override_accepts_planning_reviewer(self):
        """Epic-level override for planning_reviewer role succeeds."""
        conn = _setup_db()
        pid = _insert_project(conn)
        eid = _insert_epic(conn, pid)

        _insert_override(conn, pid, "planning_reviewer", "be", "cmd", epic_id=eid)
        row = conn.execute(
            "SELECT role FROM role_overrides WHERE epic_id = ? AND role = 'planning_reviewer'",
            (eid,),
        ).fetchone()
        assert row is not None
        conn.close()

    def test_ticket_override_accepts_implementer(self):
        """Ticket-level override for implementer role succeeds."""
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)

        _insert_override(conn, pid, "implementer", "be", "cmd", ticket_id=tid)
        row = conn.execute(
            "SELECT role FROM role_overrides WHERE ticket_id = ?", (tid,)
        ).fetchone()
        assert row["role"] == "implementer"
        conn.close()

    def test_ticket_override_accepts_reviewer(self):
        """Ticket-level override for reviewer role succeeds."""
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)

        _insert_override(conn, pid, "reviewer", "be", "cmd", ticket_id=tid)
        row = conn.execute(
            "SELECT role FROM role_overrides WHERE ticket_id = ? AND role = 'reviewer'",
            (tid,),
        ).fetchone()
        assert row is not None
        conn.close()

    def test_unique_constraint_per_scope_role(self):
        """Cannot have two overrides for the same scope + role."""
        conn = _setup_db()
        pid = _insert_project(conn)
        tid = _insert_ticket(conn, pid)

        _insert_override(conn, pid, "implementer", "be1", "cmd1", ticket_id=tid)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_override(conn, pid, "implementer", "be2", "cmd2", ticket_id=tid)
        conn.close()
