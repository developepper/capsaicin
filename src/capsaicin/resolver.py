"""Adapter resolution service.

Selects the correct AdapterConfig for a given role by walking a
deterministic precedence chain:

    ticket override → epic override → project config.toml → built-in fallback

Epic-level overrides are scoped to ``planner`` and ``planning_reviewer``.
Ticket-level overrides are scoped to ``implementer`` and ``reviewer``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from capsaicin.config import AdapterConfig, Config

# Built-in fallback: Claude Code with the default ``claude`` command.
_FALLBACK = AdapterConfig(backend="claude-code", command="claude")

VALID_OVERRIDE_ROLES = frozenset(
    {"implementer", "reviewer", "planner", "planning_reviewer"}
)

_EPIC_ROLES = frozenset({"planner", "planning_reviewer"})
_TICKET_ROLES = frozenset({"implementer", "reviewer"})


@dataclass
class ResolvedAdapter:
    """An AdapterConfig together with the source that provided it."""

    config: AdapterConfig
    source: str  # e.g. "ticket override", "epic override", "project config", "fallback"


def _row_to_adapter_config(row: sqlite3.Row) -> AdapterConfig:
    """Convert a role_overrides row to an AdapterConfig."""
    allowed_tools_raw = row["allowed_tools"]
    allowed_tools: list[str] = (
        json.loads(allowed_tools_raw) if allowed_tools_raw else []
    )
    return AdapterConfig(
        backend=row["backend"],
        command=row["command"],
        model=row["model"],
        allowed_tools=allowed_tools,
    )


@dataclass
class RoleOverride:
    """A persisted role override row."""

    id: str
    project_id: str
    role: str
    backend: str
    command: str
    epic_id: str | None = None
    ticket_id: str | None = None
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "epic_id": self.epic_id,
            "ticket_id": self.ticket_id,
            "role": self.role,
            "backend": self.backend,
            "command": self.command,
            "model": self.model,
            "allowed_tools": self.allowed_tools,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RoleOverride:
        allowed_tools = d.get("allowed_tools") or []
        if isinstance(allowed_tools, str):
            allowed_tools = json.loads(allowed_tools)
        return cls(
            id=d["id"],
            project_id=d["project_id"],
            epic_id=d.get("epic_id"),
            ticket_id=d.get("ticket_id"),
            role=d["role"],
            backend=d["backend"],
            command=d["command"],
            model=d.get("model"),
            allowed_tools=allowed_tools,
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )


def _row_to_role_override(row: sqlite3.Row) -> RoleOverride:
    """Convert a role_overrides row to a RoleOverride dataclass."""
    allowed_tools_raw = row["allowed_tools"]
    allowed_tools: list[str] = (
        json.loads(allowed_tools_raw) if allowed_tools_raw else []
    )
    return RoleOverride(
        id=row["id"],
        project_id=row["project_id"],
        epic_id=row["epic_id"],
        ticket_id=row["ticket_id"],
        role=row["role"],
        backend=row["backend"],
        command=row["command"],
        model=row["model"],
        allowed_tools=allowed_tools,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ------------------------------------------------------------------
# Override CRUD helpers
# ------------------------------------------------------------------


def set_override(
    conn: sqlite3.Connection,
    *,
    override_id: str,
    project_id: str,
    role: str,
    backend: str,
    command: str,
    epic_id: str | None = None,
    ticket_id: str | None = None,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
) -> str:
    """Insert or replace a role override. Returns the override ID.

    Enforces role-scope rules: epic overrides for planner/planning_reviewer,
    ticket overrides for implementer/reviewer. Uses delete-then-insert to
    handle the two separate UNIQUE constraints cleanly.
    """
    if role not in VALID_OVERRIDE_ROLES:
        raise ValueError(f"Invalid override role: {role!r}")
    if epic_id and ticket_id:
        raise ValueError("Exactly one of epic_id or ticket_id must be set.")
    if not epic_id and not ticket_id:
        raise ValueError("Exactly one of epic_id or ticket_id must be set.")
    if epic_id and role not in _EPIC_ROLES:
        raise ValueError(
            f"Epic overrides only support planner/planning_reviewer, got {role!r}"
        )
    if ticket_id and role not in _TICKET_ROLES:
        raise ValueError(
            f"Ticket overrides only support implementer/reviewer, got {role!r}"
        )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tools_json = json.dumps(allowed_tools) if allowed_tools else None

    # Delete any existing override for this scope + role (upsert).
    if epic_id:
        conn.execute(
            "DELETE FROM role_overrides WHERE epic_id = ? AND role = ?",
            (epic_id, role),
        )
    else:
        conn.execute(
            "DELETE FROM role_overrides WHERE ticket_id = ? AND role = ?",
            (ticket_id, role),
        )

    conn.execute(
        "INSERT INTO role_overrides "
        "(id, project_id, epic_id, ticket_id, role, backend, command, "
        "model, allowed_tools, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            override_id,
            project_id,
            epic_id,
            ticket_id,
            role,
            backend,
            command,
            model,
            tools_json,
            now,
            now,
        ),
    )

    return override_id


def get_overrides_for_epic(
    conn: sqlite3.Connection,
    epic_id: str,
) -> list[RoleOverride]:
    """Return all role overrides scoped to an epic."""
    try:
        rows = conn.execute(
            "SELECT id, project_id, epic_id, ticket_id, role, backend, "
            "command, model, allowed_tools, created_at, updated_at "
            "FROM role_overrides WHERE epic_id = ? ORDER BY role",
            (epic_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_role_override(r) for r in rows]


def get_overrides_for_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
) -> list[RoleOverride]:
    """Return all role overrides scoped to a ticket."""
    try:
        rows = conn.execute(
            "SELECT id, project_id, epic_id, ticket_id, role, backend, "
            "command, model, allowed_tools, created_at, updated_at "
            "FROM role_overrides WHERE ticket_id = ? ORDER BY role",
            (ticket_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_role_override(r) for r in rows]


def delete_override(
    conn: sqlite3.Connection,
    override_id: str,
) -> bool:
    """Delete a role override by ID. Returns True if a row was deleted."""
    try:
        cursor = conn.execute(
            "DELETE FROM role_overrides WHERE id = ?",
            (override_id,),
        )
    except sqlite3.OperationalError:
        return False
    return cursor.rowcount > 0


def _lookup_ticket_override(
    conn: sqlite3.Connection,
    ticket_id: str,
    role: str,
) -> AdapterConfig | None:
    """Look up a ticket-level override for *role*.

    Returns None gracefully if the role_overrides table does not exist
    (older DBs that have not yet run migration 0006).
    """
    try:
        row = conn.execute(
            "SELECT backend, command, model, allowed_tools "
            "FROM role_overrides WHERE ticket_id = ? AND role = ?",
            (ticket_id, role),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return _row_to_adapter_config(row)


def _lookup_epic_override(
    conn: sqlite3.Connection,
    epic_id: str,
    role: str,
) -> AdapterConfig | None:
    """Look up an epic-level override for *role*.

    Returns None gracefully if the role_overrides table does not exist
    (older DBs that have not yet run migration 0006).
    """
    try:
        row = conn.execute(
            "SELECT backend, command, model, allowed_tools "
            "FROM role_overrides WHERE epic_id = ? AND role = ?",
            (epic_id, role),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return _row_to_adapter_config(row)


def _config_for_role(config: Config, role: str) -> AdapterConfig | None:
    """Return the project-level AdapterConfig for *role*, or None."""
    if role == "implementer":
        return config.implementer
    if role == "reviewer":
        return config.reviewer
    if role == "planner":
        return config.resolved_planner
    if role == "planning_reviewer":
        return config.resolved_planning_reviewer
    return None


def resolve_adapter_config(
    config: Config,
    role: str,
    conn: sqlite3.Connection | None = None,
    ticket_id: str | None = None,
    epic_id: str | None = None,
) -> AdapterConfig:
    """Resolve the AdapterConfig for *role* using the precedence chain.

    Precedence (highest first):
    1. Ticket override  (implementer / reviewer only)
    2. Epic override    (planner / planning_reviewer only)
    3. Project config.toml
    4. Built-in fallback
    """
    if role not in VALID_OVERRIDE_ROLES:
        raise ValueError(f"Invalid role for adapter resolution: {role!r}")

    # 1. Ticket override (implementer/reviewer)
    if conn is not None and ticket_id is not None and role in _TICKET_ROLES:
        override = _lookup_ticket_override(conn, ticket_id, role)
        if override is not None:
            return override

    # 2. Epic override (planner/planning_reviewer)
    if conn is not None and epic_id is not None and role in _EPIC_ROLES:
        override = _lookup_epic_override(conn, epic_id, role)
        if override is not None:
            return override

    # 3. Project config
    project_config = _config_for_role(config, role)
    if project_config is not None:
        return project_config

    # 4. Fallback
    return _FALLBACK


def lookup_epic_id_for_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
) -> str | None:
    """Look up the epic_id for a ticket via its planned_ticket linkage.

    Returns None if the ticket has no planned_ticket or the planned_ticket
    has no epic.
    """
    row = conn.execute(
        "SELECT t.planned_ticket_id FROM tickets t WHERE t.id = ?",
        (ticket_id,),
    ).fetchone()
    if not row or not row["planned_ticket_id"]:
        return None
    ep_row = conn.execute(
        "SELECT epic_id FROM planned_tickets WHERE id = ?",
        (row["planned_ticket_id"],),
    ).fetchone()
    if not ep_row:
        return None
    return ep_row["epic_id"]


def resolve_all_roles(
    config: Config,
    conn: sqlite3.Connection | None = None,
    ticket_id: str | None = None,
    epic_id: str | None = None,
) -> dict[str, ResolvedAdapter]:
    """Resolve AdapterConfig and source label for all four roles.

    Returns a dict keyed by role name.
    """
    result: dict[str, ResolvedAdapter] = {}

    for role in ("implementer", "reviewer", "planner", "planning_reviewer"):
        resolved = _resolve_with_source(
            config=config,
            role=role,
            conn=conn,
            ticket_id=ticket_id,
            epic_id=epic_id,
        )
        result[role] = resolved

    return result


def _resolve_with_source(
    config: Config,
    role: str,
    conn: sqlite3.Connection | None = None,
    ticket_id: str | None = None,
    epic_id: str | None = None,
) -> ResolvedAdapter:
    """Resolve AdapterConfig for *role*, returning both config and source label."""
    # 1. Ticket override (implementer/reviewer)
    if conn is not None and ticket_id is not None and role in _TICKET_ROLES:
        override = _lookup_ticket_override(conn, ticket_id, role)
        if override is not None:
            return ResolvedAdapter(config=override, source="ticket override")

    # 2. Epic override (planner/planning_reviewer)
    if conn is not None and epic_id is not None and role in _EPIC_ROLES:
        override = _lookup_epic_override(conn, epic_id, role)
        if override is not None:
            return ResolvedAdapter(config=override, source="epic override")

    # 3. Project config
    project_config = _config_for_role(config, role)
    if project_config is not None:
        return ResolvedAdapter(config=project_config, source="project config")

    # 4. Fallback
    return ResolvedAdapter(config=_FALLBACK, source="fallback")
