"""Tests for T04 — ticket detail and diagnostics views.

Covers:
- refined TicketDetailData read model with diagnostics, diff, and cost
- ticket detail web route rendering all sections
- diagnostic visibility in the web view
- navigation from dashboard to ticket detail
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from capsaicin.app.queries.ticket_detail import (
    RunDiagnosticSummary,
    TicketDetailData,
    WorkspaceSummary,
    get_ticket_detail,
)
from capsaicin.web.app import create_app
from tests.conftest import add_ticket
from tests.workspace_helpers import (
    break_worktree as _break_worktree,
    enable_workspace as _enable_workspace,
)


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
# Read model tests
# ---------------------------------------------------------------------------


class TestTicketDetailReadModel:
    def test_basic_fields(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Read Model Test", criteria=["c1"])

        data = get_ticket_detail(env["conn"], tid)

        assert isinstance(data, TicketDetailData)
        assert data.ticket["title"] == "Read Model Test"
        assert data.ticket["status"] == "ready"
        assert len(data.criteria) == 1
        assert data.criteria[0]["description"] == "c1"

    def test_not_found_raises(self, project_env):
        env = project_env
        with pytest.raises(ValueError, match="not found"):
            get_ticket_detail(env["conn"], "nonexistent")

    def test_no_run_has_no_diagnostic(self, project_env):
        env = project_env
        tid = add_ticket(env, title="No Run")

        data = get_ticket_detail(env["conn"], tid)

        assert data.last_run is None
        assert data.last_run_diagnostic is None
        assert data.diagnostic is None
        assert data.diff_summary is None

    def test_run_diagnostic_summary(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Diag Test")
        _insert_run_with_metadata(env, tid, cost=1.23, denials=True)

        data = get_ticket_detail(env["conn"], tid)

        assert data.last_run is not None
        assert data.last_run_diagnostic is not None
        assert isinstance(data.last_run_diagnostic, RunDiagnosticSummary)
        assert data.last_run_diagnostic.cost_usd == 1.23
        assert data.last_run_diagnostic.denial_summary is not None
        assert "Edit" in data.last_run_diagnostic.denial_summary

    def test_run_diagnostic_agent_text(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Agent Text Test")
        _insert_run_with_metadata(
            env, tid, agent_result="Please grant write permission."
        )

        data = get_ticket_detail(env["conn"], tid)

        assert data.last_run_diagnostic is not None
        assert data.last_run_diagnostic.agent_text == "Please grant write permission."

    def test_diff_summary_populated(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Diff Test")
        run_id = _insert_run_with_metadata(env, tid)
        _insert_diff(env, run_id, ["src/main.py", "tests/test_main.py"])

        data = get_ticket_detail(env["conn"], tid)

        assert data.diff_summary is not None
        assert data.diff_summary.files_changed == [
            "src/main.py",
            "tests/test_main.py",
        ]
        assert data.diff_summary.diff_text is not None

    def test_verbose_includes_history(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Verbose Test")
        _insert_run_with_metadata(env, tid)
        _move_to_human_gate(env, tid)

        data = get_ticket_detail(env["conn"], tid, verbose=True)

        assert data.run_history is not None
        assert len(data.run_history) >= 1
        assert data.transition_history is not None
        assert len(data.transition_history) >= 1

    def test_gate_reason_visible(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Gate Test")
        _move_to_human_gate(env, tid, gate_reason="review_passed")

        data = get_ticket_detail(env["conn"], tid)

        assert data.ticket["gate_reason"] == "review_passed"

    def test_blocked_reason_visible(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Blocked Test")
        _move_to_blocked(env, tid)

        data = get_ticket_detail(env["conn"], tid)

        assert data.ticket["blocked_reason"] == "implementation_failure"

    def test_findings_grouped_by_severity(self, project_env):
        env = project_env
        tid = add_ticket(env, title="Findings Test")
        _insert_run_with_metadata(env, tid)
        _insert_finding(env, tid, severity="blocking", category="test")
        _insert_finding(env, tid, severity="warning", category="style")

        data = get_ticket_detail(env["conn"], tid)

        assert "blocking" in data.open_findings
        assert "warning" in data.open_findings
        assert len(data.open_findings["blocking"]) == 1
        assert len(data.open_findings["warning"]) == 1


# ---------------------------------------------------------------------------
# Web route tests
# ---------------------------------------------------------------------------


class TestTicketDetailRoute:
    def test_renders_full_detail(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Full Detail", criteria=["c1", "c2"])

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Full Detail" in resp.text
        assert "Acceptance Criteria" in resp.text
        assert "c1" in resp.text
        assert "c2" in resp.text

    def test_shows_status_badge(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Badge Test")

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "status-ready" in resp.text

    def test_shows_gate_reason(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Gate View")
        _move_to_human_gate(env, tid, gate_reason="review_passed")

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Gate Reason" in resp.text
        assert "review_passed" in resp.text

    def test_shows_blocked_reason(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Blocked View")
        _move_to_blocked(env, tid)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Blocked Reason" in resp.text
        assert "implementation_failure" in resp.text

    def test_shows_last_run_with_cost(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Cost View")
        _insert_run_with_metadata(env, tid, cost=0.42)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Last Run" in resp.text
        assert "$0.4200" in resp.text

    def test_shows_denial_summary(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Denial View")
        _insert_run_with_metadata(env, tid, denials=True)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Denials" in resp.text
        assert "Edit" in resp.text

    def test_shows_agent_text(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Agent Text View")
        _insert_run_with_metadata(
            env, tid, agent_result="Grant write permission to proceed."
        )

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Agent text" in resp.text
        assert "Grant write permission" in resp.text

    def test_shows_diff_summary(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Diff View")
        run_id = _insert_run_with_metadata(env, tid)
        _insert_diff(env, run_id, ["app.py", "test_app.py"])

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Implementation Diff" in resp.text
        assert "app.py" in resp.text
        assert "test_app.py" in resp.text
        assert "2 files changed" in resp.text

    def test_shows_run_history(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="History View")
        _insert_run_with_metadata(env, tid)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Run History" in resp.text
        assert "implementer" in resp.text

    def test_shows_transition_history(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Transition View")
        _move_to_human_gate(env, tid)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Transition History" in resp.text
        assert "implementing" in resp.text
        assert "human-gate" in resp.text

    def test_shows_findings(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Findings View")
        run_id = _insert_run_with_metadata(env, tid)
        _insert_finding(
            env,
            tid,
            run_id=run_id,
            severity="blocking",
            category="correctness",
            description="Missing null check",
        )

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Open Findings" in resp.text
        assert "blocking" in resp.text
        assert "Missing null check" in resp.text

    def test_404_for_missing_ticket(self, web_client):
        client, _env = web_client
        resp = client.get("/tickets/nonexistent")
        assert resp.status_code == 404

    def test_back_to_dashboard_link(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Nav Test")

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Back to dashboard" in resp.text


# ---------------------------------------------------------------------------
# Workspace state visibility (AC-1)
# ---------------------------------------------------------------------------


class TestWorkspaceSummaryReadModel:
    """WorkspaceSummary dataclass validates expected field semantics."""

    def test_active_workspace_summary(self):
        ws = WorkspaceSummary(
            isolation_mode="worktree",
            status="active",
            branch_name="capsaicin/t1",
            worktree_path="/tmp/wt",
        )
        assert ws.isolation_mode == "worktree"
        assert ws.status == "active"
        assert not ws.needs_recovery
        assert not ws.needs_cleanup

    def test_failed_workspace_needs_recovery(self):
        ws = WorkspaceSummary(
            isolation_mode="none",
            status="failed",
            failure_reason="missing_worktree",
            failure_detail="directory gone",
            needs_recovery=True,
            needs_cleanup=True,
        )
        assert ws.needs_recovery
        assert ws.failure_reason == "missing_worktree"

    def test_shared_mode_no_workspace_fields(self):
        ws = WorkspaceSummary(isolation_mode="shared")
        assert ws.status is None
        assert ws.branch_name is None
        assert not ws.needs_recovery


class TestWorkspaceDetailRoute:
    """Ticket detail page renders workspace state when isolation is active."""

    def test_shared_workspace_section_shown(self, web_client):
        """When workspace isolation is disabled, workspace panel shows shared mode."""
        client, env = web_client
        tid = add_ticket(env, title="Shared Mode")

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "workspace-panel" in resp.text
        assert "shared" in resp.text

    def test_workspace_section_with_active_workspace(self, web_client):
        """When a workspace exists, detail page shows it."""
        client, env = web_client
        tid = add_ticket(env, title="Workspace View")

        # Enable workspace isolation in config
        _enable_workspace(env)

        # Create a real workspace
        ws = _create_workspace(env, tid)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "workspace-panel" in resp.text
        assert "worktree" in resp.text
        assert f"capsaicin/{tid}" in resp.text

    def test_workspace_failure_shows_actionable_message(self, web_client):
        """When workspace is failed, detail page shows specific failure guidance."""
        client, env = web_client
        tid = add_ticket(env, title="Failed WS")

        _enable_workspace(env)
        ws = _create_workspace(env, tid)

        # Simulate workspace failure by removing the worktree
        _break_worktree(env, ws.worktree_path)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "workspace-failure" in resp.text
        assert "missing" in resp.text.lower() or "worktree" in resp.text.lower()

    def test_workspace_recovery_button_shown(self, web_client):
        """When workspace is failed, a Recover button is shown."""
        client, env = web_client
        tid = add_ticket(env, title="Recover Button")

        _enable_workspace(env)
        ws = _create_workspace(env, tid)
        _break_worktree(env, ws.worktree_path)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Recover Workspace" in resp.text
        assert f"/tickets/{tid}/workspace/recover" in resp.text

    def test_workspace_cleanup_button_on_active(self, web_client):
        """Active workspace shows Clean Up button."""
        client, env = web_client
        tid = add_ticket(env, title="Cleanup Button")

        _enable_workspace(env)
        _create_workspace(env, tid)

        resp = client.get(f"/tickets/{tid}")
        assert resp.status_code == 200
        assert "Clean Up Workspace" in resp.text
        assert f"/tickets/{tid}/workspace/cleanup" in resp.text


class TestDashboardToDetailNavigation:
    def test_dashboard_links_to_ticket_detail(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Nav Target")

        resp = client.get("/")
        assert resp.status_code == 200
        assert f"/tickets/{tid}" in resp.text

    def test_human_gate_links_to_ticket(self, web_client):
        client, env = web_client
        tid = add_ticket(env, title="Gate Nav")
        _move_to_human_gate(env, tid)

        resp = client.get("/")
        assert f"/tickets/{tid}" in resp.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_run_with_metadata(
    env,
    ticket_id,
    *,
    cost=None,
    denials=False,
    agent_result=None,
    role="implementer",
    exit_status="success",
):
    """Insert a fake agent run with optional adapter metadata."""
    from capsaicin.queries import generate_id, now_utc

    run_id = generate_id()
    meta = {}
    if cost is not None:
        meta["total_cost_usd"] = cost
    if denials:
        meta["normalized_denials"] = [
            {"tool_name": "Edit", "tool_use_id": "t1", "file_path": "/app/main.py"},
        ]
        exit_status = "permission_denied"

    envelope = {}
    if agent_result:
        envelope["result"] = agent_result

    env["conn"].execute(
        "INSERT INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, started_at, finished_at, "
        "duration_seconds, adapter_metadata, raw_stdout) "
        "VALUES (?, ?, ?, 'read-write', 1, 1, ?, 'test', '{}', ?, ?, 5.0, ?, ?)",
        (
            run_id,
            ticket_id,
            role,
            exit_status,
            now_utc(),
            now_utc(),
            json.dumps(meta) if meta else None,
            json.dumps(envelope) if envelope else None,
        ),
    )
    env["conn"].commit()
    return run_id


def _insert_diff(
    env, run_id, files, diff_text="--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new"
):
    """Insert a fake run_diffs record."""
    env["conn"].execute(
        "INSERT INTO run_diffs (run_id, diff_text, files_changed) VALUES (?, ?, ?)",
        (run_id, diff_text, json.dumps(files)),
    )
    env["conn"].commit()


def _insert_finding(
    env,
    ticket_id,
    *,
    run_id=None,
    severity="blocking",
    category="test",
    description="Test finding",
    location=None,
):
    """Insert a fake open finding."""
    from capsaicin.queries import generate_id, now_utc

    if run_id is None:
        # Find the last run for this ticket
        row = (
            env["conn"]
            .execute(
                "SELECT id FROM agent_runs WHERE ticket_id = ? "
                "ORDER BY started_at DESC LIMIT 1",
                (ticket_id,),
            )
            .fetchone()
        )
        run_id = row["id"] if row else generate_id()

    fid = generate_id()
    now = now_utc()
    env["conn"].execute(
        "INSERT INTO findings "
        "(id, run_id, ticket_id, severity, category, location, "
        "fingerprint, description, disposition, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
        (
            fid,
            run_id,
            ticket_id,
            severity,
            category,
            location,
            f"{category}|{location or ''}|{description[:80].lower()}",
            description,
            now,
            now,
        ),
    )
    env["conn"].commit()


def _move_to_human_gate(env, ticket_id, gate_reason="review_passed"):
    from capsaicin.state_machine import transition_ticket

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
    from capsaicin.state_machine import transition_ticket

    transition_ticket(env["conn"], ticket_id, "implementing", "system", reason="test")
    transition_ticket(
        env["conn"],
        ticket_id,
        "blocked",
        "system",
        reason="test",
        blocked_reason="implementation_failure",
    )


def _create_workspace(env, ticket_id):
    """Create a workspace for a ticket (commit_setup + create)."""
    from tests.workspace_helpers import commit_setup, create_workspace_for_ticket

    commit_setup(env)
    return create_workspace_for_ticket(env, ticket_id)
