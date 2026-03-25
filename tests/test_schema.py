"""Tests for the MVP schema migration (T03)."""

from __future__ import annotations

import sqlite3

import pytest

from capsaicin.db import get_connection, run_migrations

EXPECTED_TABLES = {
    "projects",
    "tickets",
    "acceptance_criteria",
    "ticket_dependencies",
    "agent_runs",
    "run_diffs",
    "review_baselines",
    "findings",
    "orchestrator_state",
    "state_transitions",
    "decisions",
    "planned_epics",
    "planned_tickets",
    "planned_ticket_criteria",
    "planned_ticket_dependencies",
    "planning_findings",
    "materialization_hashes",
    "backend_evidence",
    "evidence_requirements",
    "role_overrides",
    "agent_run_evidence",
    "evidence_requirement_events",
    "workspaces",
}

EXPECTED_INDEXES = {
    "idx_tickets_project_status",
    "idx_agent_runs_ticket_role",
    "idx_findings_ticket_disposition",
    "idx_findings_run",
    "idx_findings_criterion",
    "idx_findings_fingerprint",
    "idx_state_transitions_ticket",
    "idx_acceptance_criteria_ticket",
    "idx_ticket_deps_depends_on",
    "idx_orchestrator_state_active_ticket",
    "idx_planned_epics_project_status",
    "idx_planned_tickets_epic",
    "idx_planning_findings_epic_disposition",
    "idx_planning_findings_run",
    "idx_planning_findings_fingerprint",
    "idx_planned_ticket_criteria_ticket",
    "idx_planned_ticket_deps_depends_on",
    "idx_agent_runs_epic",
    "idx_state_transitions_epic",
    "idx_backend_evidence_epic",
    "idx_backend_evidence_ticket",
    "idx_backend_evidence_type",
    "idx_evidence_requirements_epic",
    "idx_evidence_requirements_ticket",
    "idx_evidence_requirements_status",
    "idx_role_overrides_epic",
    "idx_role_overrides_ticket",
    "idx_role_overrides_project",
    "idx_agent_run_evidence_evidence",
    "idx_evidence_requirement_events_req",
    "idx_workspaces_project_status",
    "idx_workspaces_ticket",
    "idx_workspaces_epic",
    "idx_agent_runs_workspace",
}


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    run_migrations(c)
    yield c
    c.close()


# --- Helper to insert prerequisite rows ---


def _insert_project(conn, project_id="p1"):
    conn.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
        (project_id, "test", "/tmp/repo"),
    )


def _insert_ticket(conn, ticket_id="t1", project_id="p1", status="ready"):
    conn.execute(
        "INSERT INTO tickets (id, project_id, title, description, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticket_id, project_id, "title", "desc", status),
    )


def _insert_run(conn, run_id="r1", ticket_id="t1"):
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'implementer', 'read-write', 1, 'running', 'p', '{}', datetime('now'))",
        (run_id, ticket_id),
    )


def _insert_criterion(conn, criterion_id="ac1", ticket_id="t1"):
    conn.execute(
        "INSERT INTO acceptance_criteria (id, ticket_id, description) VALUES (?, ?, ?)",
        (criterion_id, ticket_id, "criterion"),
    )


# --- Table and index existence ---


class TestSchemaCreation:
    def test_all_tables_exist(self, conn):
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND substr(name,1,1) != '_' AND name != 'sqlite_sequence'"
            )
        }
        assert tables == EXPECTED_TABLES

    def test_all_indexes_exist(self, conn):
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            )
        }
        assert indexes == EXPECTED_INDEXES

    def test_migration_is_idempotent(self, conn):
        run_migrations(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND substr(name,1,1) != '_' AND name != 'sqlite_sequence'"
            )
        }
        assert tables == EXPECTED_TABLES


# --- CHECK constraints ---


class TestCheckConstraints:
    def test_ticket_status_rejects_invalid(self, conn):
        _insert_project(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_ticket(conn, status="invalid")

    def test_ticket_status_accepts_all_valid(self, conn):
        _insert_project(conn)
        valid = [
            "ready",
            "implementing",
            "in-review",
            "revise",
            "human-gate",
            "pr-ready",
            "blocked",
            "done",
        ]
        for i, status in enumerate(valid):
            _insert_ticket(conn, ticket_id=f"t{i}", status=status)

    def test_ticket_gate_reason_rejects_invalid(self, conn):
        _insert_project(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tickets (id, project_id, title, description, gate_reason) "
                "VALUES ('t1', 'p1', 'title', 'desc', 'bad_reason')"
            )

    def test_ticket_gate_reason_accepts_valid(self, conn):
        _insert_project(conn)
        valid = [
            "review_passed",
            "reviewer_escalated",
            "cycle_limit",
            "implementation_failure",
            "human_requested",
            "empty_implementation",
            "low_confidence_pass",
        ]
        for i, reason in enumerate(valid):
            conn.execute(
                "INSERT INTO tickets (id, project_id, title, description, gate_reason) "
                "VALUES (?, 'p1', 'title', 'desc', ?)",
                (f"t{i}", reason),
            )

    def test_acceptance_criteria_status_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO acceptance_criteria (id, ticket_id, description, status) "
                "VALUES ('ac1', 't1', 'test', 'invalid')"
            )

    def test_acceptance_criteria_status_accepts_valid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        for i, status in enumerate(["pending", "met", "unmet", "disputed"]):
            conn.execute(
                "INSERT INTO acceptance_criteria (id, ticket_id, description, status) "
                "VALUES (?, 't1', 'test', ?)",
                (f"ac{i}", status),
            )

    def test_agent_run_role_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agent_runs "
                "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, run_request, started_at) "
                "VALUES ('r1', 't1', 'bad_role', 'read-write', 1, 'running', 'p', '{}', datetime('now'))"
            )

    def test_agent_run_mode_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agent_runs "
                "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, run_request, started_at) "
                "VALUES ('r1', 't1', 'implementer', 'bad_mode', 1, 'running', 'p', '{}', datetime('now'))"
            )

    def test_agent_run_exit_status_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agent_runs "
                "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, run_request, started_at) "
                "VALUES ('r1', 't1', 'implementer', 'read-write', 1, 'bad_status', 'p', '{}', datetime('now'))"
            )

    def test_finding_severity_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        _insert_run(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings "
                "(id, run_id, ticket_id, severity, category, fingerprint, description) "
                "VALUES ('f1', 'r1', 't1', 'critical', 'cat', 'fp', 'desc')"
            )

    def test_finding_disposition_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        _insert_run(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings "
                "(id, run_id, ticket_id, severity, category, fingerprint, description, disposition) "
                "VALUES ('f1', 'r1', 't1', 'blocking', 'cat', 'fp', 'desc', 'invalid')"
            )

    def test_orchestrator_state_status_rejects_invalid(self, conn):
        _insert_project(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO orchestrator_state (project_id, status) VALUES ('p1', 'bad')"
            )

    def test_decision_type_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO decisions (id, ticket_id, decision) VALUES ('d1', 't1', 'invalid')"
            )

    def test_agent_run_xor_both_set_rejected(self, conn):
        """Cannot set both ticket_id and epic_id on agent_runs."""
        _insert_project(conn)
        _insert_ticket(conn)
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, status) "
            "VALUES ('pe1', 'p1', 'problem', 'new')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agent_runs "
                "(id, ticket_id, epic_id, role, mode, cycle_number, "
                "exit_status, prompt, run_request, started_at) "
                "VALUES ('r1', 't1', 'pe1', 'implementer', 'read-write', "
                "1, 'running', 'p', '{}', datetime('now'))"
            )

    def test_agent_run_xor_both_null_rejected(self, conn):
        """Cannot have both ticket_id and epic_id null on agent_runs."""
        _insert_project(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agent_runs "
                "(id, ticket_id, epic_id, role, mode, cycle_number, "
                "exit_status, prompt, run_request, started_at) "
                "VALUES ('r1', NULL, NULL, 'implementer', 'read-write', "
                "1, 'running', 'p', '{}', datetime('now'))"
            )

    def test_decision_xor_both_set_rejected(self, conn):
        """Cannot set both ticket_id and epic_id on decisions."""
        _insert_project(conn)
        _insert_ticket(conn)
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, status) "
            "VALUES ('pe1', 'p1', 'problem', 'new')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO decisions (id, ticket_id, epic_id, decision) "
                "VALUES ('d1', 't1', 'pe1', 'approve')"
            )

    def test_state_transition_xor_both_set_rejected(self, conn):
        """Cannot set both ticket_id and epic_id on state_transitions."""
        _insert_project(conn)
        _insert_ticket(conn)
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, status) "
            "VALUES ('pe1', 'p1', 'problem', 'new')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO state_transitions "
                "(ticket_id, epic_id, from_status, to_status, triggered_by) "
                "VALUES ('t1', 'pe1', 'ready', 'implementing', 'system')"
            )

    def test_workspace_status_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, status) "
                "VALUES ('w1', 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', 'invalid')"
            )

    def test_workspace_status_accepts_valid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        non_failed = ["pending", "setting_up", "active", "tearing_down", "cleaned"]
        for i, status in enumerate(non_failed):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, status) "
                "VALUES (?, 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', ?)",
                (f"w{i}", status),
            )
        # 'failed' requires a failure_reason
        conn.execute(
            "INSERT INTO workspaces "
            "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, "
            "status, failure_reason) "
            "VALUES ('wf', 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', "
            "'failed', 'setup_failure')"
        )

    def test_workspace_failure_reason_rejects_invalid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, "
                "status, failure_reason) "
                "VALUES ('w1', 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', "
                "'failed', 'bad_reason')"
            )

    def test_workspace_failure_reason_accepts_valid(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        valid = [
            "dirty_base_repo",
            "missing_worktree",
            "branch_drift",
            "setup_failure",
            "cleanup_conflict",
        ]
        for i, reason in enumerate(valid):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, "
                "status, failure_reason) "
                "VALUES (?, 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', "
                "'failed', ?)",
                (f"w{i}", reason),
            )

    def test_workspace_failed_without_reason_rejected(self, conn):
        """status='failed' requires failure_reason to be set."""
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, "
                "status) "
                "VALUES ('w1', 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', "
                "'failed')"
            )

    def test_workspace_reason_without_failed_rejected(self, conn):
        """Non-failed status must not have a failure_reason."""
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, "
                "status, failure_reason) "
                "VALUES ('w1', 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', "
                "'active', 'setup_failure')"
            )

    def test_workspace_xor_both_set_rejected(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, status) "
            "VALUES ('pe1', 'p1', 'problem', 'new')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, epic_id, worktree_path, branch_name, base_ref) "
                "VALUES ('w1', 'p1', 't1', 'pe1', '/tmp/wt', 'capsaicin/t1', 'main')"
            )

    def test_workspace_xor_both_null_rejected(self, conn):
        _insert_project(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, worktree_path, branch_name, base_ref) "
                "VALUES ('w1', 'p1', '/tmp/wt', 'capsaicin/t1', 'main')"
            )

    def test_self_dependency_rejected(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES ('t1', 't1')"
            )


# --- FK constraints ---


class TestForeignKeys:
    def test_ticket_requires_valid_project(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_ticket(conn, project_id="nonexistent")

    def test_acceptance_criteria_requires_valid_ticket(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_criterion(conn, ticket_id="nonexistent")

    def test_agent_run_requires_valid_ticket(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(conn, ticket_id="nonexistent")

    def test_run_diffs_requires_valid_run(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO run_diffs (run_id, diff_text, files_changed) "
                "VALUES ('nonexistent', 'diff', '[]')"
            )

    def test_review_baselines_requires_valid_run(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO review_baselines (run_id, baseline_diff, baseline_status) "
                "VALUES ('nonexistent', 'diff', 'status')"
            )

    def test_finding_requires_valid_run(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings "
                "(id, run_id, ticket_id, severity, category, fingerprint, description) "
                "VALUES ('f1', 'nonexistent', 't1', 'blocking', 'cat', 'fp', 'desc')"
            )

    def test_finding_requires_valid_ticket(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        _insert_run(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings "
                "(id, run_id, ticket_id, severity, category, fingerprint, description) "
                "VALUES ('f1', 'r1', 'nonexistent', 'blocking', 'cat', 'fp', 'desc')"
            )

    def test_finding_criterion_fk(self, conn):
        _insert_project(conn)
        _insert_ticket(conn)
        _insert_run(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings "
                "(id, run_id, ticket_id, acceptance_criterion_id, severity, category, fingerprint, description) "
                "VALUES ('f1', 'r1', 't1', 'nonexistent', 'blocking', 'cat', 'fp', 'desc')"
            )

    def test_orchestrator_state_requires_valid_project(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO orchestrator_state (project_id, status) VALUES ('nonexistent', 'idle')"
            )

    def test_state_transition_requires_valid_ticket(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO state_transitions (ticket_id, from_status, to_status, triggered_by) "
                "VALUES ('nonexistent', 'ready', 'implementing', 'system')"
            )

    def test_decision_requires_valid_ticket(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO decisions (id, ticket_id, decision) VALUES ('d1', 'nonexistent', 'approve')"
            )

    def test_ticket_dependency_requires_valid_tickets(self, conn):
        _insert_project(conn)
        _insert_ticket(conn, ticket_id="t1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES ('t1', 'nonexistent')"
            )

    def test_workspace_requires_valid_project(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref) "
                "VALUES ('w1', 'nonexistent', 't1', '/tmp/wt', 'capsaicin/t1', 'main')"
            )

    def test_workspace_requires_valid_ticket(self, conn):
        _insert_project(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref) "
                "VALUES ('w1', 'p1', 'nonexistent', '/tmp/wt', 'capsaicin/t1', 'main')"
            )

    def test_agent_run_workspace_fk(self, conn):
        """agent_runs.workspace_id must reference a valid workspace."""
        _insert_project(conn)
        _insert_ticket(conn)
        _insert_run(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE agent_runs SET workspace_id = 'nonexistent' WHERE id = 'r1'"
            )

    def test_agent_run_workspace_nullable(self, conn):
        """Pre-isolation runs have workspace_id = NULL."""
        _insert_project(conn)
        _insert_ticket(conn)
        _insert_run(conn)
        row = conn.execute(
            "SELECT workspace_id FROM agent_runs WHERE id = 'r1'"
        ).fetchone()
        assert row["workspace_id"] is None

    def test_agent_run_workspace_coherence_same_ticket(self, conn):
        """Run can reference a workspace with the same ticket_id."""
        _insert_project(conn)
        _insert_ticket(conn)
        conn.execute(
            "INSERT INTO workspaces "
            "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, status) "
            "VALUES ('w1', 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', 'active')"
        )
        conn.execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, "
            "run_request, started_at, workspace_id) "
            "VALUES ('r1', 't1', 'implementer', 'read-write', 1, 'running', "
            "'p', '{}', datetime('now'), 'w1')"
        )

    def test_agent_run_workspace_coherence_different_ticket_rejected(self, conn):
        """Run cannot reference a workspace with a different ticket_id."""
        _insert_project(conn)
        _insert_ticket(conn, ticket_id="t1")
        _insert_ticket(conn, ticket_id="t2")
        conn.execute(
            "INSERT INTO workspaces "
            "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, status) "
            "VALUES ('w1', 'p1', 't2', '/tmp/wt', 'capsaicin/t2', 'main', 'active')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="workspace does not belong"):
            conn.execute(
                "INSERT INTO agent_runs "
                "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, "
                "run_request, started_at, workspace_id) "
                "VALUES ('r1', 't1', 'implementer', 'read-write', 1, 'running', "
                "'p', '{}', datetime('now'), 'w1')"
            )

    def test_agent_run_workspace_coherence_update_rejected(self, conn):
        """Updating workspace_id to a mismatched workspace is rejected."""
        _insert_project(conn)
        _insert_ticket(conn, ticket_id="t1")
        _insert_ticket(conn, ticket_id="t2")
        conn.execute(
            "INSERT INTO workspaces "
            "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, status) "
            "VALUES ('w1', 'p1', 't1', '/tmp/wt1', 'capsaicin/t1', 'main', 'active')"
        )
        conn.execute(
            "INSERT INTO workspaces "
            "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, status) "
            "VALUES ('w2', 'p1', 't2', '/tmp/wt2', 'capsaicin/t2', 'main', 'active')"
        )
        conn.execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, "
            "run_request, started_at, workspace_id) "
            "VALUES ('r1', 't1', 'implementer', 'read-write', 1, 'running', "
            "'p', '{}', datetime('now'), 'w1')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="workspace does not belong"):
            conn.execute("UPDATE agent_runs SET workspace_id = 'w2' WHERE id = 'r1'")

    def test_workspace_project_coherence_ticket_mismatch_rejected(self, conn):
        """Workspace project_id must match the ticket's project_id."""
        _insert_project(conn, project_id="p1")
        _insert_project(conn, project_id="p2")
        _insert_ticket(conn, ticket_id="t1", project_id="p1")
        with pytest.raises(sqlite3.IntegrityError, match="workspace project_id"):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, ticket_id, worktree_path, branch_name, base_ref) "
                "VALUES ('w1', 'p2', 't1', '/tmp/wt', 'capsaicin/t1', 'main')"
            )

    def test_workspace_project_coherence_epic_mismatch_rejected(self, conn):
        """Workspace project_id must match the epic's project_id."""
        _insert_project(conn, project_id="p1")
        _insert_project(conn, project_id="p2")
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, status) "
            "VALUES ('pe1', 'p1', 'problem', 'new')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="workspace project_id"):
            conn.execute(
                "INSERT INTO workspaces "
                "(id, project_id, epic_id, worktree_path, branch_name, base_ref) "
                "VALUES ('w1', 'p2', 'pe1', '/tmp/wt', 'capsaicin/pe1', 'main')"
            )

    def test_workspace_project_coherence_matching_accepted(self, conn):
        """Workspace with matching project_id succeeds."""
        _insert_project(conn)
        _insert_ticket(conn)
        conn.execute(
            "INSERT INTO workspaces "
            "(id, project_id, ticket_id, worktree_path, branch_name, base_ref) "
            "VALUES ('w1', 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main')"
        )

    def test_agent_run_retarget_ticket_with_workspace_rejected(self, conn):
        """Updating ticket_id on a run with workspace_id revalidates coherence."""
        _insert_project(conn)
        _insert_ticket(conn, ticket_id="t1")
        _insert_ticket(conn, ticket_id="t2")
        conn.execute(
            "INSERT INTO workspaces "
            "(id, project_id, ticket_id, worktree_path, branch_name, base_ref, status) "
            "VALUES ('w1', 'p1', 't1', '/tmp/wt', 'capsaicin/t1', 'main', 'active')"
        )
        conn.execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, exit_status, prompt, "
            "run_request, started_at, workspace_id) "
            "VALUES ('r1', 't1', 'implementer', 'read-write', 1, 'running', "
            "'p', '{}', datetime('now'), 'w1')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="workspace does not belong"):
            conn.execute("UPDATE agent_runs SET ticket_id = 't2' WHERE id = 'r1'")

    def test_agent_run_retarget_epic_with_workspace_rejected(self, conn):
        """Updating epic_id on a run with workspace_id revalidates coherence."""
        _insert_project(conn)
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, status) "
            "VALUES ('pe1', 'p1', 'problem', 'new')"
        )
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, status) "
            "VALUES ('pe2', 'p1', 'problem2', 'new')"
        )
        conn.execute(
            "INSERT INTO workspaces "
            "(id, project_id, epic_id, worktree_path, branch_name, base_ref, status) "
            "VALUES ('w1', 'p1', 'pe1', '/tmp/wt', 'capsaicin/pe1', 'main', 'active')"
        )
        conn.execute(
            "INSERT INTO agent_runs "
            "(id, epic_id, role, mode, cycle_number, exit_status, prompt, "
            "run_request, started_at, workspace_id) "
            "VALUES ('r1', 'pe1', 'planner', 'read-only', 1, 'running', "
            "'p', '{}', datetime('now'), 'w1')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="workspace does not belong"):
            conn.execute("UPDATE agent_runs SET epic_id = 'pe2' WHERE id = 'r1'")

    def test_valid_dependency_succeeds(self, conn):
        _insert_project(conn)
        _insert_ticket(conn, ticket_id="t1")
        _insert_ticket(conn, ticket_id="t2")
        conn.execute(
            "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) VALUES ('t1', 't2')"
        )
