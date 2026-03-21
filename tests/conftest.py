"""Shared test fixtures and helpers."""

from __future__ import annotations

import subprocess

import pytest

from capsaicin.config import load_config
from capsaicin.db import get_connection
from capsaicin.init import init_project
from capsaicin.ticket_add import _get_project_id, add_ticket_inline


@pytest.fixture()
def project_env(tmp_path):
    """Set up a project with a git repo, returning context dict."""
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


def add_ticket(env, title="Test ticket", desc="Do something", criteria=None):
    """Add a ticket, returning its ID. criteria defaults to []."""
    return add_ticket_inline(
        env["conn"],
        env["project_id"],
        title,
        desc,
        criteria if criteria is not None else [],
        env["log_path"],
    )


def get_ticket(conn, ticket_id):
    """Fetch a ticket row as a dict (superset of commonly needed columns)."""
    return dict(
        conn.execute(
            "SELECT id, project_id, title, description, status, "
            "current_cycle, current_impl_attempt, current_review_attempt, "
            "gate_reason, blocked_reason "
            "FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
    )


def get_ticket_status(conn, ticket_id):
    """Return the current status string for a ticket."""
    return conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()["status"]


def run_impl_to_in_review(env, ticket_id=None):
    """Run implementation pipeline to get a ticket into in-review status."""
    from tests.adapters import DiffProducingAdapter
    from capsaicin.ticket_run import run_implementation_pipeline

    if ticket_id is None:
        ticket_id = add_ticket(env)
    ticket = get_ticket(env["conn"], ticket_id)
    adapter = DiffProducingAdapter(env["repo"])
    final = run_implementation_pipeline(
        conn=env["conn"],
        project_id=env["project_id"],
        ticket=ticket,
        config=env["config"],
        adapter=adapter,
        log_path=env["log_path"],
    )
    assert final == "in-review"
    return ticket_id
