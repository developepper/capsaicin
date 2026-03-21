"""Shared project resolution for CLI commands.

Extracts the repeated project-resolution boilerplate into a single helper
so each CLI command doesn't duplicate the same 15-20 lines.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from capsaicin.config import Config, ConfigError, load_config, resolve_project
from capsaicin.db import get_connection


@dataclass
class ProjectContext:
    """Resolved project paths and resources."""

    slug: str
    project_dir: Path
    db_path: Path
    config_path: Path
    log_path: Path

    # Lazily opened — call open() / close() or use as context manager
    _conn: sqlite3.Connection | None = None
    _config: Config | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = get_connection(self.db_path)
        return self._conn

    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = load_config(self.config_path)
        return self._config

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> ProjectContext:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_project_id(self) -> str:
        """Fetch the single project ID from the database."""
        row = self.conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
        if row is None:
            raise ConfigError("No project found in database.")
        return row["id"]


def resolve_context(
    repo_path: str | None = None,
    project_slug: str | None = None,
) -> ProjectContext:
    """Resolve project paths from repo_path and optional project_slug.

    Raises ConfigError if the project cannot be resolved.
    """
    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise ConfigError(f"Project '{slug}' not found at {project_dir}")
    else:
        slug = resolve_project(capsaicin_root)

    project_dir = capsaicin_root / "projects" / slug

    return ProjectContext(
        slug=slug,
        project_dir=project_dir,
        db_path=project_dir / "capsaicin.db",
        config_path=project_dir / "config.toml",
        log_path=project_dir / "activity.log",
    )
