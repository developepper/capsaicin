"""Domain exception hierarchy for capsaicin.

All domain-specific errors inherit from ``CapsaicinError`` so that CLI
code and programmatic consumers can catch at the appropriate level.
"""


class CapsaicinError(Exception):
    """Base class for all capsaicin domain errors."""


class TicketNotFoundError(CapsaicinError):
    """Raised when a ticket ID does not exist."""

    def __init__(self, ticket_id: str) -> None:
        self.ticket_id = ticket_id
        super().__init__(f"Ticket '{ticket_id}' not found.")


class InvalidStatusError(CapsaicinError):
    """Raised when a ticket is not in the expected status for an operation."""

    def __init__(
        self, ticket_id: str, actual: str, expected: str | tuple[str, ...]
    ) -> None:
        self.ticket_id = ticket_id
        self.actual = actual
        self.expected = expected
        if isinstance(expected, str):
            exp_str = f"'{expected}'"
        else:
            exp_str = " or ".join(f"'{s}'" for s in expected)
        super().__init__(
            f"Ticket '{ticket_id}' is in '{actual}' status; expected {exp_str}."
        )


class NoEligibleTicketError(CapsaicinError):
    """Raised when no ticket matches the selection criteria."""


class DependencyCycleError(CapsaicinError):
    """Raised when adding a dependency would create a cycle."""


class PlannedEpicNotFoundError(CapsaicinError):
    """Raised when a planned epic ID does not exist."""

    def __init__(self, epic_id: str) -> None:
        self.epic_id = epic_id
        super().__init__(f"Planned epic '{epic_id}' not found.")
