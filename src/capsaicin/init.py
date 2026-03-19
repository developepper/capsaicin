"""Project initialization logic for `capsaicin init`."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ulid import ULID

from capsaicin.activity_log import log_event
from capsaicin.config import config_to_snapshot, load_config, write_default_config
from capsaicin.db import get_connection, run_migrations


def slugify(name: str) -> str:
    """Convert a project name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def init_project(project_name: str, repo_path: str | None = None) -> Path:
    """Initialize a capsaicin project, returning the project directory path.

    Raises click.ClickException (via caller) or ValueError on errors.
    """
    slug = slugify(project_name)
    if not slug:
        raise ValueError(f"Project name '{project_name}' produces an empty slug.")

    # Resolve repo path to absolute
    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    # Create directory structure
    capsaicin_root = Path(repo_path) / ".capsaicin"
    project_dir = capsaicin_root / "projects" / slug

    if project_dir.exists():
        raise ValueError(f"Project '{slug}' already exists at {project_dir}")

    project_dir.mkdir(parents=True)
    (project_dir / "renders" / "epics").mkdir(parents=True)
    (project_dir / "renders" / "tickets").mkdir(parents=True)
    (project_dir / "renders" / "reviews").mkdir(parents=True)
    (project_dir / "exports" / "github" / "issues").mkdir(parents=True)
    (project_dir / "exports" / "github" / "prs").mkdir(parents=True)

    # Write default config
    config_path = project_dir / "config.toml"
    write_default_config(config_path, project_name, repo_path)

    # Create and migrate database
    db_path = project_dir / "capsaicin.db"
    conn = get_connection(db_path)
    try:
        run_migrations(conn)

        # Load config for DB snapshot
        config = load_config(config_path)
        config_snapshot = json.dumps(config_to_snapshot(config))

        # Insert project row
        project_id = str(ULID())
        conn.execute(
            "INSERT INTO projects (id, name, repo_path, config) VALUES (?, ?, ?, ?)",
            (project_id, project_name, repo_path, config_snapshot),
        )

        # Insert orchestrator_state row
        conn.execute(
            "INSERT INTO orchestrator_state (project_id, status) VALUES (?, 'idle')",
            (project_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # Create activity log and write init event
    log_path = project_dir / "activity.log"
    log_event(log_path, "PROJECT_INIT", project_id=project_id)

    return project_dir
