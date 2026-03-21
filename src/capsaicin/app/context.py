"""Shared application context for CLI and web handlers.

Wraps ``ProjectContext`` with eager project-ID resolution, config
refresh, and a consistent interface that both delivery layers can use
without reimplementing discovery logic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from capsaicin.config import Config, refresh_config_snapshot
from capsaicin.project_context import ProjectContext, resolve_context


@dataclass
class AppContext:
    """Resolved application context with project ID and live config."""

    project_id: str
    conn: sqlite3.Connection
    config: Config
    log_path: Path

    def refresh_config(self) -> None:
        """Refresh the DB config snapshot from the on-disk config."""
        refresh_config_snapshot(self.conn, self.config)

    @classmethod
    def from_project_context(cls, ctx: ProjectContext) -> AppContext:
        """Build an AppContext from an already-resolved ProjectContext."""
        return cls(
            project_id=ctx.get_project_id(),
            conn=ctx.conn,
            config=ctx.config,
            log_path=ctx.log_path,
        )

    @classmethod
    def resolve(
        cls,
        repo_path: str | None = None,
        project_slug: str | None = None,
    ) -> tuple[ProjectContext, AppContext]:
        """Resolve project paths and return both the owning ProjectContext
        and a ready-to-use AppContext.

        The caller should use the ProjectContext as a context manager to
        manage the DB connection lifetime::

            pctx, app = AppContext.resolve()
            with pctx:
                result = some_command(app)
        """
        pctx = resolve_context(repo_path, project_slug)
        app = cls.from_project_context(pctx)
        return pctx, app
