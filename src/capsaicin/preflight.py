"""Preflight check module for environment and repo validation (T04).

Reusable, side-effect-free helpers that validate the local environment
before expensive agent runs.  Intended for use by ``doctor``, ``init``,
and any future setup commands.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Structured check result
# ---------------------------------------------------------------------------

STATUSES = frozenset({"pass", "warn", "fail"})


@dataclass
class CheckResult:
    """Outcome of a single preflight check."""

    name: str
    status: str  # "pass", "warn", or "fail"
    message: str
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(
                f"Invalid check status: '{self.status}'. "
                f"Must be one of: {sorted(STATUSES)}"
            )


@dataclass
class PreflightReport:
    """Aggregated results of all preflight checks."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if no check failed."""
        return all(c.status != "fail" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warn" for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "warn"]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_command_on_path(command: str) -> CheckResult:
    """Check whether *command* is available on PATH."""
    if shutil.which(command):
        return CheckResult(
            name="command_available",
            status="pass",
            message=f"'{command}' found on PATH.",
        )
    return CheckResult(
        name="command_available",
        status="fail",
        message=f"'{command}' not found on PATH.",
        detail=f"Ensure '{command}' is installed and available in your shell.",
    )


def check_repo_path_exists(repo_path: str | Path) -> CheckResult:
    """Check whether the configured repo path exists."""
    p = Path(repo_path)
    if p.is_dir():
        return CheckResult(
            name="repo_path_exists",
            status="pass",
            message=f"Repo path exists: {p}",
        )
    return CheckResult(
        name="repo_path_exists",
        status="fail",
        message=f"Repo path does not exist: {p}",
    )


def check_is_git_repo(repo_path: str | Path) -> CheckResult:
    """Check whether *repo_path* is a git repository."""
    p = Path(repo_path)
    if not p.is_dir():
        return CheckResult(
            name="is_git_repo",
            status="fail",
            message=f"Not a directory: {p}",
        )
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return CheckResult(
                name="is_git_repo",
                status="pass",
                message="Directory is a git repository.",
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return CheckResult(
        name="is_git_repo",
        status="fail",
        message=f"Not a git repository: {p}",
        detail="Run 'git init' or check the configured repo_path.",
    )


def check_working_tree_clean(repo_path: str | Path) -> CheckResult:
    """Check working tree cleanliness.  Dirty state is a warning, not a failure."""
    p = Path(repo_path)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return CheckResult(
                name="working_tree_clean",
                status="warn",
                message="Could not determine working tree status.",
                detail=result.stderr.strip() or None,
            )
        if result.stdout.strip() == "":
            return CheckResult(
                name="working_tree_clean",
                status="pass",
                message="Working tree is clean.",
            )
        changed_count = len(result.stdout.strip().splitlines())
        return CheckResult(
            name="working_tree_clean",
            status="warn",
            message=f"Working tree has {changed_count} uncommitted change(s).",
            detail="Uncommitted changes may affect implementation runs.",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return CheckResult(
            name="working_tree_clean",
            status="warn",
            message="Could not check working tree status.",
        )


def check_claude_permissions(repo_path: str | Path) -> CheckResult:
    """Check Claude local permission configuration for required write tools.

    Reads ``<repo>/.claude/settings.local.json`` and verifies that
    ``permissions.allow`` contains bare-string entries for ``Edit`` and
    ``Write``.

    Bash entries are not validated beyond presence of the settings file
    because required Bash allow-list patterns are project-specific and
    not safely inferable.
    """
    settings_path = Path(repo_path) / ".claude" / "settings.local.json"

    if not settings_path.is_file():
        return CheckResult(
            name="claude_permissions",
            status="fail",
            message="Claude settings file not found.",
            detail=(
                f"Expected: {settings_path}\n"
                "Create it with permissions.allow including 'Edit' and 'Write'."
            ),
        )

    try:
        raw = settings_path.read_text()
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        return CheckResult(
            name="claude_permissions",
            status="fail",
            message="Could not parse Claude settings file.",
            detail=str(exc),
        )

    if not isinstance(data, dict):
        return CheckResult(
            name="claude_permissions",
            status="fail",
            message="Claude settings file is not a JSON object.",
        )

    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        return CheckResult(
            name="claude_permissions",
            status="fail",
            message="Missing 'permissions' key in Claude settings.",
            detail="Add a 'permissions' object with an 'allow' array.",
        )

    allow_list = permissions.get("allow")
    if not isinstance(allow_list, list):
        return CheckResult(
            name="claude_permissions",
            status="fail",
            message="Missing 'permissions.allow' array in Claude settings.",
            detail="Add an 'allow' array containing 'Edit' and 'Write'.",
        )

    # Check for bare Edit and Write entries.
    # Filter to strings only — the array may contain non-string or
    # non-hashable values (numbers, objects) in malformed settings.
    allow_strings = {e for e in allow_list if isinstance(e, str)}
    missing = []
    for tool in ("Edit", "Write"):
        if tool not in allow_strings:
            missing.append(tool)

    if missing:
        missing_str = ", ".join(missing)
        return CheckResult(
            name="claude_permissions",
            status="fail",
            message=f"Missing required tool permission(s): {missing_str}",
            detail=(f"Add {missing_str} to permissions.allow in {settings_path}"),
        )

    return CheckResult(
        name="claude_permissions",
        status="pass",
        message="Claude write permissions configured (Edit, Write).",
    )


# ---------------------------------------------------------------------------
# Workspace isolation readiness
# ---------------------------------------------------------------------------


def check_workspace_readiness(
    repo_path: str | Path,
    workspace_enabled: bool = False,
) -> CheckResult:
    """Check whether workspace isolation prerequisites are met.

    When ``workspace_enabled`` is True, verifies that git supports
    worktrees and the ``.worktrees`` directory is writable.  When
    disabled, reports a pass with an informational message.
    """
    if not workspace_enabled:
        return CheckResult(
            name="workspace_readiness",
            status="pass",
            message="Workspace isolation is disabled (shared repo mode).",
        )

    p = Path(repo_path)

    # Check git worktree support (requires git >= 2.5).
    try:
        result = subprocess.run(
            ["git", "worktree", "list"],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return CheckResult(
                name="workspace_readiness",
                status="fail",
                message="git worktree not supported or not a git repo.",
                detail=result.stderr.strip() or "git worktree list failed.",
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return CheckResult(
            name="workspace_readiness",
            status="fail",
            message="Could not verify git worktree support.",
        )

    # Check .worktrees directory is writable (or can be created).
    worktrees_dir = p / ".worktrees"
    if worktrees_dir.exists() and not worktrees_dir.is_dir():
        return CheckResult(
            name="workspace_readiness",
            status="fail",
            message=".worktrees exists but is not a directory.",
            detail=f"Remove or rename {worktrees_dir} to allow workspace isolation.",
        )

    # Check writability without mutating the filesystem.
    if worktrees_dir.is_dir():
        if not os.access(worktrees_dir, os.W_OK):
            return CheckResult(
                name="workspace_readiness",
                status="fail",
                message=".worktrees directory is not writable.",
                detail=f"Check filesystem permissions on {worktrees_dir}.",
            )
    else:
        # Directory does not exist — check parent is writable.
        if not os.access(p, os.W_OK):
            return CheckResult(
                name="workspace_readiness",
                status="fail",
                message="Cannot create .worktrees directory.",
                detail=f"Check filesystem permissions on {p}.",
            )

    # Check git metadata is writable (needed for refs during worktree add).
    git_dir = p / ".git"
    if git_dir.is_dir():
        refs_dir = git_dir / "refs"
        check_target = refs_dir if refs_dir.is_dir() else git_dir
        if not os.access(check_target, os.W_OK):
            return CheckResult(
                name="workspace_readiness",
                status="fail",
                message="Git metadata directory is not writable.",
                detail=f"Check filesystem permissions on {check_target}.",
            )

    # Warn if working tree is dirty (blocks worktree creation).
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if status_result.returncode == 0 and status_result.stdout.strip():
            return CheckResult(
                name="workspace_readiness",
                status="warn",
                message="Workspace isolation enabled but working tree is dirty.",
                detail=(
                    "Uncommitted changes block worktree creation. "
                    "Commit or stash changes before running workspace-isolated pipelines."
                ),
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return CheckResult(
        name="workspace_readiness",
        status="pass",
        message="Workspace isolation prerequisites met (git worktree supported).",
    )


# ---------------------------------------------------------------------------
# Aggregated preflight
# ---------------------------------------------------------------------------


def run_preflight(
    repo_path: str | Path,
    adapter_command: str = "claude",
    workspace_enabled: bool = False,
) -> PreflightReport:
    """Run all preflight checks and return a structured report."""
    report = PreflightReport()
    report.checks.append(check_command_on_path(adapter_command))
    report.checks.append(check_repo_path_exists(repo_path))
    report.checks.append(check_is_git_repo(repo_path))
    report.checks.append(check_working_tree_clean(repo_path))
    report.checks.append(check_claude_permissions(repo_path))
    report.checks.append(check_workspace_readiness(repo_path, workspace_enabled))
    return report
