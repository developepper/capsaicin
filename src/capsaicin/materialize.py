"""Plan materialization — approved plans to implementation tickets.

Converts an approved planned epic into:
1. Markdown ticket docs under ``docs/tickets/generated/<epic-slug>/``
2. Implementation-loop DB records (tickets, acceptance_criteria,
   ticket_dependencies) with lineage back to planned_ticket IDs.

Content-hash gating prevents silent destruction of manual edits on
re-materialization.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from capsaicin.activity_log import log_event
from capsaicin.queries import (
    decode_text_list,
    generate_id,
    load_planned_epic,
    load_planned_ticket_criteria,
    load_planned_tickets,
    now_utc,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class MaterializationConflict:
    """A file that was skipped because it has manual edits."""

    file_path: str
    planned_ticket_id: str | None


@dataclass
class MaterializationResult:
    """Outcome of a materialization run."""

    epic_id: str
    output_dir: str
    tickets_created: int
    docs_written: int
    conflicts: list[MaterializationConflict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------


def _slugify(title: str) -> str:
    """Convert an epic title into a filesystem-safe slug."""
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-") or "untitled"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _render_epic_readme(
    epic: dict,
    tickets: list[dict],
    ticket_deps: dict[str, list[int]],
) -> str:
    """Render the epic-level README.md."""
    lines: list[str] = []
    title = epic.get("title") or "Untitled Epic"
    lines.append(f"# {title}")
    lines.append("")

    if epic.get("summary"):
        lines.append("## Summary")
        lines.append("")
        lines.append(epic["summary"])
        lines.append("")

    if epic.get("success_outcome"):
        lines.append("## Success Outcome")
        lines.append("")
        lines.append(epic["success_outcome"])
        lines.append("")

    if tickets:
        lines.append("## Tickets")
        lines.append("")
        lines.append("| # | Title | Dependencies |")
        lines.append("|---|-------|--------------|")
        for t in tickets:
            seq = t["sequence"]
            label = f"T{seq:02d}"
            deps = ticket_deps.get(t["id"], [])
            dep_str = ", ".join(f"T{d:02d}" for d in sorted(deps)) if deps else "-"
            lines.append(f"| [{label}](./{label}.md) | {t['title']} | {dep_str} |")
        lines.append("")

    if epic.get("sequencing_notes"):
        lines.append("## Sequencing Notes")
        lines.append("")
        lines.append(epic["sequencing_notes"])
        lines.append("")

    return "\n".join(lines)


def _render_ticket_doc(
    epic: dict,
    ticket: dict,
    criteria: list[dict],
    dep_sequences: list[int],
) -> str:
    """Render a single implementation-ticket markdown doc."""
    seq = ticket["sequence"]
    label = f"T{seq:02d}"
    lines: list[str] = []

    lines.append(f"# {label}: {ticket['title']}")
    lines.append("")

    # Goal
    lines.append("## Goal")
    lines.append("")
    lines.append(ticket["goal"])
    lines.append("")

    # Scope
    scope = decode_text_list(ticket["scope"])
    if scope:
        lines.append("## Scope")
        lines.append("")
        for item in scope:
            lines.append(f"- {item}")
        lines.append("")

    # Non-goals
    non_goals = decode_text_list(ticket["non_goals"])
    if non_goals:
        lines.append("## Non-goals")
        lines.append("")
        for item in non_goals:
            lines.append(f"- {item}")
        lines.append("")

    # Acceptance Criteria
    if criteria:
        lines.append("## Acceptance Criteria")
        lines.append("")
        for c in criteria:
            lines.append(f"- {c['description']}")
        lines.append("")

    # Dependencies
    if dep_sequences:
        lines.append("## Dependencies")
        lines.append("")
        for d in sorted(dep_sequences):
            lines.append(f"- [T{d:02d}](./T{d:02d}.md)")
        lines.append("")

    # References
    refs = decode_text_list(ticket["references_"])
    if refs:
        lines.append("## References")
        lines.append("")
        for ref in refs:
            lines.append(f"- {ref}")
        lines.append("")

    # Implementation Notes
    impl_notes = decode_text_list(ticket["implementation_notes"])
    if impl_notes:
        lines.append("## Implementation Notes")
        lines.append("")
        for note in impl_notes:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Hash-gated file writing
# ---------------------------------------------------------------------------


def _content_hash(content: str) -> str:
    """SHA-256 hex digest of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_with_hash_gate(
    conn: sqlite3.Connection,
    epic_id: str,
    planned_ticket_id: str | None,
    file_path: Path,
    content: str,
    force: bool,
) -> MaterializationConflict | None:
    """Write a file using hash-gating rules.

    Returns a ``MaterializationConflict`` if the file was skipped, else None.
    """
    new_hash = _content_hash(content)
    rel_path = str(file_path)

    # Check for existing hash record
    row = conn.execute(
        "SELECT content_hash FROM materialization_hashes "
        "WHERE epic_id = ? AND file_path = ?",
        (epic_id, rel_path),
    ).fetchone()

    if file_path.exists():
        if row is not None:
            # File exists and we have a prior hash
            current_content = file_path.read_text(encoding="utf-8")
            current_hash = _content_hash(current_content)
            if current_hash != row["content_hash"] and not force:
                # Manual edits detected
                return MaterializationConflict(
                    file_path=rel_path,
                    planned_ticket_id=planned_ticket_id,
                )
        elif not force:
            # File exists but no hash record — treat as manually created
            return MaterializationConflict(
                file_path=rel_path,
                planned_ticket_id=planned_ticket_id,
            )

    # Write the file
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    # Upsert the hash record
    ts = now_utc()
    conn.execute(
        "INSERT INTO materialization_hashes "
        "(epic_id, planned_ticket_id, file_path, content_hash, materialized_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (epic_id, file_path) DO UPDATE SET "
        "planned_ticket_id = excluded.planned_ticket_id, "
        "content_hash = excluded.content_hash, "
        "materialized_at = excluded.materialized_at",
        (epic_id, planned_ticket_id, rel_path, new_hash, ts),
    )

    return None


def _check_hash_gate_conflict(
    conn: sqlite3.Connection,
    epic_id: str,
    planned_ticket_id: str | None,
    file_path: Path,
    force: bool,
) -> MaterializationConflict | None:
    """Check whether writing a file would conflict under hash gating."""
    if force or not file_path.exists():
        return None

    rel_path = str(file_path)
    row = conn.execute(
        "SELECT content_hash FROM materialization_hashes "
        "WHERE epic_id = ? AND file_path = ?",
        (epic_id, rel_path),
    ).fetchone()
    if row is None:
        return MaterializationConflict(
            file_path=rel_path,
            planned_ticket_id=planned_ticket_id,
        )

    current_content = file_path.read_text(encoding="utf-8")
    current_hash = _content_hash(current_content)
    if current_hash != row["content_hash"]:
        return MaterializationConflict(
            file_path=rel_path,
            planned_ticket_id=planned_ticket_id,
        )

    return None


# ---------------------------------------------------------------------------
# Implementation-ticket DB record creation
# ---------------------------------------------------------------------------


def _create_impl_tickets(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    tickets: list[dict],
    ticket_criteria: dict[str, list[dict]],
    ticket_deps: dict[str, list[str]],
) -> dict[str, str]:
    """Create implementation-loop ticket DB records.

    Returns a mapping of planned_ticket_id -> implementation ticket_id.
    """
    planned_to_impl: dict[str, str] = {}

    for pt in tickets:
        planned_id = pt["id"]

        # Check if an implementation ticket already exists for this planned ticket
        existing = conn.execute(
            "SELECT id FROM tickets WHERE planned_ticket_id = ?",
            (planned_id,),
        ).fetchone()
        if existing:
            ticket_id = existing["id"]
            planned_to_impl[planned_id] = ticket_id

            conn.execute(
                "UPDATE tickets SET title = ?, description = ?, updated_at = ? "
                "WHERE id = ?",
                (pt["title"], pt["goal"], now_utc(), ticket_id),
            )
            conn.execute(
                "DELETE FROM acceptance_criteria WHERE ticket_id = ?",
                (ticket_id,),
            )
        else:
            ticket_id = generate_id()
            planned_to_impl[planned_id] = ticket_id

            conn.execute(
                "INSERT INTO tickets "
                "(id, project_id, title, description, status, planned_ticket_id) "
                "VALUES (?, ?, ?, ?, 'ready', ?)",
                (ticket_id, project_id, pt["title"], pt["goal"], planned_id),
            )

            conn.execute(
                "INSERT INTO state_transitions "
                "(ticket_id, from_status, to_status, triggered_by, reason) "
                "VALUES (?, 'null', 'ready', 'system', 'materialized from plan')",
                (ticket_id,),
            )

        for criterion in ticket_criteria.get(planned_id, []):
            criterion_id = generate_id()
            conn.execute(
                "INSERT INTO acceptance_criteria "
                "(id, ticket_id, description, status) "
                "VALUES (?, ?, ?, 'pending')",
                (criterion_id, ticket_id, criterion["description"]),
            )

    # Insert dependencies (second pass to ensure all tickets exist)
    for pt in tickets:
        planned_id = pt["id"]
        impl_id = planned_to_impl[planned_id]

        conn.execute("DELETE FROM ticket_dependencies WHERE ticket_id = ?", (impl_id,))

        for dep_planned_id in ticket_deps.get(planned_id, []):
            dep_impl_id = planned_to_impl.get(dep_planned_id)
            if not dep_impl_id:
                continue

            conn.execute(
                "INSERT INTO ticket_dependencies (ticket_id, depends_on_id) "
                "VALUES (?, ?)",
                (impl_id, dep_impl_id),
            )

    return planned_to_impl


# ---------------------------------------------------------------------------
# Main materialization entry point
# ---------------------------------------------------------------------------


def materialize_epic(
    conn: sqlite3.Connection,
    project_id: str,
    epic_id: str,
    repo_root: Path,
    force: bool = False,
    log_path: str | Path | None = None,
    allowed_statuses: tuple[str, ...] = ("approved",),
) -> MaterializationResult:
    """Materialize an approved epic into docs and implementation tickets.

    Args:
        conn: Database connection.
        project_id: Project ID.
        epic_id: The planned epic to materialize.
        repo_root: Repository root for writing doc files.
        force: Overwrite files with manual edits.
        log_path: Optional activity log path.

    Returns:
        MaterializationResult with counts and any conflicts.

    Raises:
        ValueError: If the epic is not in ``approved`` status.
    """
    epic = load_planned_epic(conn, epic_id)
    if epic["status"] not in allowed_statuses:
        raise ValueError(
            f"Epic '{epic_id}' is in '{epic['status']}' status; "
            f"expected one of {allowed_statuses!r} for materialization."
        )

    if not epic.get("title"):
        raise ValueError(f"Epic '{epic_id}' has no title; cannot materialize.")

    tickets = load_planned_tickets(conn, epic_id)
    if not tickets:
        raise ValueError(f"Epic '{epic_id}' has no planned tickets.")

    # Load criteria for each ticket
    ticket_criteria: dict[str, list[dict]] = {}
    for t in tickets:
        ticket_criteria[t["id"]] = load_planned_ticket_criteria(conn, t["id"])

    # Load dependencies: planned_ticket_id -> list of depends_on planned_ticket_ids
    # Also build sequence lookup for doc rendering
    ticket_deps_ids: dict[str, list[str]] = {}
    ticket_deps_seqs: dict[str, list[int]] = {}
    id_to_seq: dict[str, int] = {t["id"]: t["sequence"] for t in tickets}

    dep_rows = conn.execute(
        "SELECT planned_ticket_id, depends_on_id "
        "FROM planned_ticket_dependencies "
        "WHERE planned_ticket_id IN ({})".format(",".join("?" for _ in tickets)),
        [t["id"] for t in tickets],
    ).fetchall()

    for row in dep_rows:
        ptid = row["planned_ticket_id"]
        dep_id = row["depends_on_id"]
        ticket_deps_ids.setdefault(ptid, []).append(dep_id)
        dep_seq = id_to_seq.get(dep_id)
        if dep_seq is not None:
            ticket_deps_seqs.setdefault(ptid, []).append(dep_seq)

    # Determine output directory
    slug = _slugify(epic["title"])
    output_dir = repo_root / "docs" / "tickets" / "generated" / slug
    output_dir.mkdir(parents=True, exist_ok=True)
    rel_output = str(output_dir.relative_to(repo_root))

    conflicts: list[MaterializationConflict] = []
    docs_written = 0
    planned_docs: list[tuple[str | None, Path, str]] = []

    # Render and write epic README
    readme_content = _render_epic_readme(epic, tickets, ticket_deps_seqs)
    readme_path = output_dir / "README.md"
    planned_docs.append((None, readme_path, readme_content))

    # Render and write each ticket doc
    for t in tickets:
        criteria = ticket_criteria.get(t["id"], [])
        dep_seqs = ticket_deps_seqs.get(t["id"], [])
        content = _render_ticket_doc(epic, t, criteria, dep_seqs)

        label = f"T{t['sequence']:02d}"
        file_path = output_dir / f"{label}.md"
        planned_docs.append((t["id"], file_path, content))

    for planned_ticket_id, file_path, _ in planned_docs:
        conflict = _check_hash_gate_conflict(
            conn,
            epic_id,
            planned_ticket_id,
            file_path,
            force,
        )
        if conflict:
            conflicts.append(conflict)

    if conflicts:
        return MaterializationResult(
            epic_id=epic_id,
            output_dir=rel_output,
            tickets_created=0,
            docs_written=0,
            conflicts=conflicts,
        )

    for planned_ticket_id, file_path, content in planned_docs:
        conflict = _write_with_hash_gate(
            conn, epic_id, planned_ticket_id, file_path, content, force
        )
        if conflict:
            conflicts.append(conflict)
        else:
            docs_written += 1

    # Create implementation-ticket DB records
    new_ticket_count = _count_new_tickets(conn, tickets)
    _create_impl_tickets(conn, project_id, epic_id, tickets, ticket_criteria, ticket_deps_ids)

    # Store materialized_path on the epic
    conn.execute(
        "UPDATE planned_epics SET materialized_path = ?, updated_at = ? WHERE id = ?",
        (rel_output, now_utc(), epic_id),
    )

    conn.commit()

    # Activity log
    if log_path:
        log_event(
            log_path,
            "EPIC_MATERIALIZED",
            project_id=project_id,
            payload={
                "epic_id": epic_id,
                "output_dir": rel_output,
                "tickets_created": new_ticket_count,
                "docs_written": docs_written,
                "conflicts": len(conflicts),
            },
        )

    return MaterializationResult(
        epic_id=epic_id,
        output_dir=rel_output,
        tickets_created=new_ticket_count,
        docs_written=docs_written,
        conflicts=conflicts,
    )


def _count_new_tickets(conn: sqlite3.Connection, tickets: list[dict]) -> int:
    """Count how many planned tickets don't have implementation tickets yet."""
    count = 0
    for t in tickets:
        existing = conn.execute(
            "SELECT 1 FROM tickets WHERE planned_ticket_id = ?",
            (t["id"],),
        ).fetchone()
        if not existing:
            count += 1
    return count
