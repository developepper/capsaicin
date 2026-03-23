"""Gate reason display helpers for web templates.

Centralises the mapping from gate_reason strings to human-readable
display text and rationale-required flags so that templates don't
need to carry this logic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateDisplayInfo:
    """Pre-computed gate display data for a template."""

    display_text: str
    rationale_required: bool = False
    is_warning: bool = False


# -- Ticket gate reasons ----------------------------------------------------

_TICKET_GATE_DISPLAY: dict[str, GateDisplayInfo] = {
    "review_passed": GateDisplayInfo(
        display_text=(
            "Review passed. Approve to move this ticket to PR-ready, "
            "or revise to send it back for another implementation pass."
        ),
    ),
    "low_confidence_pass": GateDisplayInfo(
        display_text="Review passed with low confidence. A rationale is required to approve.",
        rationale_required=True,
        is_warning=True,
    ),
    "reviewer_escalated": GateDisplayInfo(
        display_text="The reviewer escalated this ticket. A rationale is required to approve.",
        rationale_required=True,
        is_warning=True,
    ),
    "cycle_limit": GateDisplayInfo(
        display_text="The cycle limit was reached. A rationale is required to approve.",
        rationale_required=True,
        is_warning=True,
    ),
    "empty_implementation": GateDisplayInfo(
        display_text="The implementation produced no changes.",
        is_warning=True,
    ),
    "permission_denied": GateDisplayInfo(
        display_text="The implementation was blocked by permission denials.",
        is_warning=True,
    ),
}

_TICKET_GATE_DEFAULT = GateDisplayInfo(
    display_text="This ticket is waiting for a human decision.",
)


def get_ticket_gate_display(gate_reason: str | None) -> GateDisplayInfo:
    """Return display info for a ticket gate reason."""
    if not gate_reason:
        return _TICKET_GATE_DEFAULT
    return _TICKET_GATE_DISPLAY.get(gate_reason, _TICKET_GATE_DEFAULT)


# -- Epic gate reasons ------------------------------------------------------

_EPIC_GATE_DISPLAY: dict[str, GateDisplayInfo] = {
    "review_passed": GateDisplayInfo(
        display_text=(
            "Plan review passed. Approve to materialize implementation tickets, "
            "or revise to send it back for another planning pass."
        ),
    ),
    "low_confidence_pass": GateDisplayInfo(
        display_text="Plan review passed with low confidence.",
        is_warning=True,
    ),
    "reviewer_escalated": GateDisplayInfo(
        display_text="The planning reviewer escalated this epic.",
        is_warning=True,
    ),
    "cycle_limit": GateDisplayInfo(
        display_text="The planning cycle limit was reached.",
        is_warning=True,
    ),
    "draft_failure": GateDisplayInfo(
        display_text="The draft step failed.",
        is_warning=True,
    ),
    "human_requested": GateDisplayInfo(
        display_text="Human review was explicitly requested.",
    ),
}

_EPIC_GATE_DEFAULT = GateDisplayInfo(
    display_text="This epic is waiting for a human decision.",
)


def get_epic_gate_display(gate_reason: str | None) -> GateDisplayInfo:
    """Return display info for an epic gate reason."""
    if not gate_reason:
        return _EPIC_GATE_DEFAULT
    return _EPIC_GATE_DISPLAY.get(gate_reason, _EPIC_GATE_DEFAULT)
