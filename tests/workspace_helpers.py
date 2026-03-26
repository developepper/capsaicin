"""Shared workspace test helpers.

Consolidates git-repo, database, and workspace lifecycle helpers that were
previously duplicated across test_workspace.py, test_workspace_ops.py,
test_dashboard.py, test_ticket_detail_web.py, test_web_actions.py, and
test_ticket_approve.py.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

from capsaicin.config import Config, WorkspaceConfig
from capsaicin.db import get_connection, run_migrations


# ---------------------------------------------------------------------------
# Git repo helpers
# ---------------------------------------------------------------------------


def init_git_repo(path: Path) -> None:
    """Create a git repo with one commit at *path*."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "readme.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def make_workspace_conn():
    """Return an in-memory connection with all migrations applied."""
    conn = get_connection(":memory:")
    run_migrations(conn)
    return conn


def insert_project(conn, project_id="p1", repo_path="/tmp"):
    """Insert a minimal project row."""
    conn.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
        (project_id, "test", repo_path),
    )
    conn.commit()


def insert_ticket(conn, ticket_id="t1", project_id="p1"):
    """Insert a minimal ticket row in ``ready`` status."""
    conn.execute(
        "INSERT INTO tickets (id, project_id, title, description, status) "
        "VALUES (?, ?, 'Test', 'desc', 'ready')",
        (ticket_id, project_id),
    )
    conn.commit()


def insert_epic(conn, epic_id="e1", project_id="p1"):
    """Insert a minimal planned_epics row."""
    conn.execute(
        "INSERT INTO planned_epics (id, project_id, problem_statement, status) "
        "VALUES (?, ?, 'problem', 'new')",
        (epic_id, project_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def default_ws_config(worktree_root=None):
    """Return a standard enabled WorkspaceConfig.

    When *worktree_root* is given it overrides the default home-directory
    location, keeping tests from polluting ``~/.capsaicin/worktrees/``.
    """
    return WorkspaceConfig(
        enabled=True,
        branch_prefix="capsaicin/",
        auto_cleanup=True,
        worktree_root=str(worktree_root) if worktree_root is not None else None,
    )


def enable_workspace_config(config: Config) -> Config:
    """Return a copy of *config* with workspace isolation enabled (in-memory)."""
    return dataclasses.replace(
        config,
        workspace=WorkspaceConfig(
            enabled=True,
            branch_prefix="capsaicin/",
            auto_cleanup=True,
        ),
    )


# ---------------------------------------------------------------------------
# project_env helpers (operate on the env dict from conftest.project_env)
# ---------------------------------------------------------------------------


def enable_workspace(env) -> None:
    """Enable workspace isolation in the project config.toml file.

    Sets ``worktree_root`` to a sibling of the test repo so that worktrees
    stay inside pytest's tmp_path and never pollute ``~/.capsaicin/``.
    """
    import re

    wt_root = env["repo"].parent / "worktrees"
    config_path = env["project_dir"] / "config.toml"
    text = config_path.read_text()
    if re.search(r"(?m)^\[workspace\]\s*$", text) is None:
        text += (
            "\n[workspace]\nenabled = true\n"
            'branch_prefix = "capsaicin/"\nauto_cleanup = true\n'
            f'worktree_root = "{wt_root}"\n'
        )
    else:
        text = text.replace("enabled = false", "enabled = true")
    config_path.write_text(text)


def commit_setup(env) -> None:
    """Gitignore .capsaicin/ and commit so the base repo is clean."""
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


def create_workspace_for_ticket(env, ticket_id):
    """Create a real workspace for *ticket_id*, returning a WorkspaceReady.

    Must be called after :func:`enable_workspace` and :func:`commit_setup`.
    """
    from capsaicin.workspace import WorkspaceReady, create_workspace

    wt_root = env["repo"].parent / "worktrees"
    result = create_workspace(
        env["conn"],
        env["repo"],
        env["project_id"],
        default_ws_config(worktree_root=wt_root),
        ticket_id=ticket_id,
    )
    assert isinstance(result, WorkspaceReady), (
        f"Expected WorkspaceReady, got {type(result)}"
    )
    return result


def break_worktree(env, worktree_path) -> None:
    """Unregister a worktree from git but leave an orphan directory."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", worktree_path],
        cwd=env["repo"],
        check=True,
        capture_output=True,
    )
    Path(worktree_path).mkdir(parents=True)
