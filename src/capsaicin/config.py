"""Config loading, validation, and default generation."""

from __future__ import annotations

import json
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised when config is missing or invalid."""


@dataclass
class AdapterConfig:
    backend: str
    command: str
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class LimitsConfig:
    max_cycles: int = 3
    max_impl_retries: int = 2
    max_review_retries: int = 2
    timeout_seconds: int = 300


@dataclass
class ReviewerConfig:
    mode: str = "read-only"


@dataclass
class TicketSelectionConfig:
    order: str = "created_at"


@dataclass
class PathsConfig:
    renders_dir: str = "renders"
    exports_dir: str = "exports"


@dataclass
class WorkspaceConfig:
    enabled: bool = False
    branch_prefix: str = "capsaicin/"
    auto_cleanup: bool = True
    worktree_root: str | None = None

    @staticmethod
    def disabled() -> WorkspaceConfig:
        """Return the default config used when isolation is not configured."""
        return WorkspaceConfig(enabled=False)


@dataclass
class ProjectConfig:
    name: str
    repo_path: str


@dataclass
class Config:
    project: ProjectConfig
    implementer: AdapterConfig
    reviewer: AdapterConfig
    limits: LimitsConfig
    reviewer_policy: ReviewerConfig
    ticket_selection: TicketSelectionConfig
    paths: PathsConfig
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig.disabled)
    planner: AdapterConfig | None = None
    planning_reviewer: AdapterConfig | None = None

    @property
    def resolved_planner(self) -> AdapterConfig:
        """Return planner config, falling back to implementer."""
        return self.planner if self.planner is not None else self.implementer

    @property
    def resolved_planning_reviewer(self) -> AdapterConfig:
        """Return planning_reviewer config, falling back to reviewer."""
        return (
            self.planning_reviewer
            if self.planning_reviewer is not None
            else self.reviewer
        )


def load_config(config_path: str | Path) -> Config:
    """Parse config.toml and return a validated Config."""
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    # Required sections
    for section in ("project", "limits"):
        if section not in raw:
            raise ConfigError(f"Missing required config section: [{section}]")

    adapters = raw.get("adapters", {})
    if "implementer" not in adapters:
        raise ConfigError("Missing required config section: [adapters.implementer]")
    if "reviewer" not in adapters:
        raise ConfigError("Missing required config section: [adapters.reviewer]")

    # Project
    proj = raw["project"]
    for req in ("name", "repo_path"):
        if req not in proj:
            raise ConfigError(f"Missing required field: [project].{req}")
    project = ProjectConfig(name=proj["name"], repo_path=proj["repo_path"])

    # Adapters
    def _parse_adapter(section: dict, label: str) -> AdapterConfig:
        for req in ("backend", "command"):
            if req not in section:
                raise ConfigError(f"Missing required field: [adapters.{label}].{req}")
        return AdapterConfig(
            backend=section["backend"],
            command=section["command"],
            model=section.get("model"),
            allowed_tools=section.get("allowed_tools", []),
        )

    implementer = _parse_adapter(adapters["implementer"], "implementer")
    reviewer = _parse_adapter(adapters["reviewer"], "reviewer")

    planner = (
        _parse_adapter(adapters["planner"], "planner")
        if "planner" in adapters
        else None
    )
    planning_reviewer = (
        _parse_adapter(adapters["planning_reviewer"], "planning_reviewer")
        if "planning_reviewer" in adapters
        else None
    )

    # Limits (with defaults)
    lim = raw["limits"]
    limits = LimitsConfig(
        max_cycles=lim.get("max_cycles", 3),
        max_impl_retries=lim.get("max_impl_retries", 2),
        max_review_retries=lim.get("max_review_retries", 2),
        timeout_seconds=lim.get("timeout_seconds", 300),
    )

    # Optional sections with defaults
    rev = raw.get("reviewer", {})
    reviewer_policy = ReviewerConfig(mode=rev.get("mode", "read-only"))

    ts = raw.get("ticket_selection", {})
    ticket_selection = TicketSelectionConfig(order=ts.get("order", "created_at"))

    p = raw.get("paths", {})
    paths = PathsConfig(
        renders_dir=p.get("renders_dir", "renders"),
        exports_dir=p.get("exports_dir", "exports"),
    )

    ws = raw.get("workspace", {})
    workspace = WorkspaceConfig(
        enabled=ws.get("enabled", False),
        branch_prefix=ws.get("branch_prefix", "capsaicin/"),
        auto_cleanup=ws.get("auto_cleanup", True),
        worktree_root=ws.get("worktree_root"),
    )

    return Config(
        project=project,
        implementer=implementer,
        reviewer=reviewer,
        limits=limits,
        reviewer_policy=reviewer_policy,
        ticket_selection=ticket_selection,
        paths=paths,
        workspace=workspace,
        planner=planner,
        planning_reviewer=planning_reviewer,
    )


def config_to_snapshot(config: Config) -> dict:
    """Serialize a Config to a dict suitable for JSON storage in projects.config."""
    return {
        "project": {
            "name": config.project.name,
            "repo_path": config.project.repo_path,
        },
        "adapters": {
            "implementer": {
                "backend": config.implementer.backend,
                "command": config.implementer.command,
                "model": config.implementer.model,
                "allowed_tools": config.implementer.allowed_tools,
            },
            "reviewer": {
                "backend": config.reviewer.backend,
                "command": config.reviewer.command,
                "model": config.reviewer.model,
                "allowed_tools": config.reviewer.allowed_tools,
            },
            "planner": {
                "backend": config.resolved_planner.backend,
                "command": config.resolved_planner.command,
                "model": config.resolved_planner.model,
                "allowed_tools": config.resolved_planner.allowed_tools,
            },
            "planning_reviewer": {
                "backend": config.resolved_planning_reviewer.backend,
                "command": config.resolved_planning_reviewer.command,
                "model": config.resolved_planning_reviewer.model,
                "allowed_tools": config.resolved_planning_reviewer.allowed_tools,
            },
        },
        "limits": {
            "max_cycles": config.limits.max_cycles,
            "max_impl_retries": config.limits.max_impl_retries,
            "max_review_retries": config.limits.max_review_retries,
            "timeout_seconds": config.limits.timeout_seconds,
        },
        "reviewer": {
            "mode": config.reviewer_policy.mode,
        },
        "ticket_selection": {
            "order": config.ticket_selection.order,
        },
        "paths": {
            "renders_dir": config.paths.renders_dir,
            "exports_dir": config.paths.exports_dir,
        },
        "workspace": {
            "enabled": config.workspace.enabled,
            "branch_prefix": config.workspace.branch_prefix,
            "auto_cleanup": config.workspace.auto_cleanup,
            **(
                {"worktree_root": config.workspace.worktree_root}
                if config.workspace.worktree_root
                else {}
            ),
        },
    }


def refresh_config_snapshot(conn: sqlite3.Connection, config: Config) -> None:
    """Refresh the projects.config DB snapshot from the current Config.

    Per configuration.md, config.toml on disk is the source of truth and
    the DB snapshot is refreshed on each command invocation.
    """
    snapshot = json.dumps(config_to_snapshot(config))
    conn.execute("UPDATE projects SET config = ?", (snapshot,))
    conn.commit()


def resolve_project(capsaicin_root: str | Path) -> str:
    """Auto-resolve the project slug if exactly one project exists.

    Returns the project slug (directory name).
    Raises ConfigError if zero or multiple projects exist.
    """
    projects_dir = Path(capsaicin_root) / "projects"
    if not projects_dir.is_dir():
        raise ConfigError(f"No projects directory found at {projects_dir}")

    projects = [d.name for d in sorted(projects_dir.iterdir()) if d.is_dir()]

    if len(projects) == 0:
        raise ConfigError("No projects found. Run 'capsaicin init' first.")
    if len(projects) > 1:
        raise ConfigError(
            f"Multiple projects found: {', '.join(projects)}. "
            "Specify a project explicitly."
        )
    return projects[0]


def _toml_escape(value: str) -> str:
    """Escape a string for use inside a TOML basic (double-quoted) string."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def write_default_config(path: str | Path, project_name: str, repo_path: str) -> None:
    """Write the default config.toml."""
    safe_name = _toml_escape(project_name)
    safe_repo = _toml_escape(repo_path)
    content = f"""\
[project]
name = "{safe_name}"
repo_path = "{safe_repo}"

[adapters.implementer]
backend = "claude-code"
command = "claude"

[adapters.reviewer]
backend = "claude-code"
command = "claude"
allowed_tools = ["Read", "Glob", "Grep", "Bash"]

[limits]
max_cycles = 3
max_impl_retries = 2
max_review_retries = 2
timeout_seconds = 300

[reviewer]
mode = "read-only"

[ticket_selection]
order = "created_at"

[paths]
renders_dir = "renders"
exports_dir = "exports"
"""
    Path(path).write_text(content)
