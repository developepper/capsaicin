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
from dataclasses import dataclass

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
