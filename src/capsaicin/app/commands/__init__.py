"""Command services — structured entry points for workflow mutations.

Each command module provides a function that wraps an existing pipeline
module and returns a ``CommandResult`` instead of printing to stdout.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandResult:
    """Structured outcome of a workflow command."""

    ticket_id: str
    final_status: str
    detail: str | None = None
    gate_reason: str | None = None
    blocked_reason: str | None = None


@dataclass
class PlanningCommandResult:
    """Structured outcome of a planning workflow command."""

    epic_id: str
    final_status: str
    detail: str | None = None
    gate_reason: str | None = None
    blocked_reason: str | None = None
