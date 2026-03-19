"""Logic for `capsaicin ticket add`."""

from __future__ import annotations

import sqlite3
import tomllib
from pathlib import Path

from ulid import ULID

from capsaicin.activity_log import log_event
from capsaicin.config import resolve_project


def _find_project(repo_path: str | None) -> tuple[Path, str]:
    """Locate the project directory and project_id.

    Returns (project_dir, project_id).
    """
    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"
    slug = resolve_project(capsaicin_root)
    project_dir = capsaicin_root / "projects" / slug
    return project_dir, repo_path


def _get_project_id(conn: sqlite3.Connection) -> str:
    """Get the single project id from the database."""
    row = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
    if row is None:
        raise ValueError("No project found in database. Run 'capsaicin init' first.")
    return row[0]


def add_ticket_inline(
    conn: sqlite3.Connection,
    project_id: str,
    title: str,
    description: str,
    criteria: list[str],
    log_path: Path,
) -> str:
    """Insert a ticket with criteria from inline CLI args. Returns ticket_id."""
    ticket_id = str(ULID())

    conn.execute(
        "INSERT INTO tickets (id, project_id, title, description, status) "
        "VALUES (?, ?, ?, ?, 'ready')",
        (ticket_id, project_id, title, description),
    )

    for criterion_desc in criteria:
        criterion_id = str(ULID())
        conn.execute(
            "INSERT INTO acceptance_criteria (id, ticket_id, description, status) "
            "VALUES (?, ?, ?, 'pending')",
            (criterion_id, ticket_id, criterion_desc),
        )

    # Record state transition
    conn.execute(
        "INSERT INTO state_transitions (ticket_id, from_status, to_status, triggered_by, reason) "
        "VALUES (?, 'null', 'ready', 'human', 'ticket created')",
        (ticket_id,),
    )

    conn.commit()

    log_event(
        log_path,
        "TICKET_CREATED",
        project_id=project_id,
        ticket_id=ticket_id,
    )

    return ticket_id


def add_ticket_from_file(
    conn: sqlite3.Connection,
    project_id: str,
    file_path: Path,
    log_path: Path,
) -> str:
    """Insert a ticket with criteria from a TOML file. Returns ticket_id."""
    with open(file_path, "rb") as f:
        data = tomllib.load(f)

    title = data.get("title")
    if not title:
        raise ValueError(f"Missing 'title' in {file_path}")
    description = data.get("description")
    if not description:
        raise ValueError(f"Missing 'description' in {file_path}")

    criteria = []
    for i, c in enumerate(data.get("criteria", [])):
        if "description" not in c or not c["description"]:
            raise ValueError(
                f"Criterion {i + 1} in {file_path} is missing a 'description' field."
            )
        criteria.append(c["description"])

    return add_ticket_inline(conn, project_id, title, description, criteria, log_path)
