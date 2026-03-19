"""Config loading, validation, and default generation."""

from __future__ import annotations

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

    return Config(
        project=project,
        implementer=implementer,
        reviewer=reviewer,
        limits=limits,
        reviewer_policy=reviewer_policy,
        ticket_selection=ticket_selection,
        paths=paths,
    )


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
