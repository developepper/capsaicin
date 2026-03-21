"""Pipeline outcome type for implementation and review pipelines.

Replaces the "_retry" / "_retry:reason" magic-string convention with
a typed return value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """Result of a single pipeline invocation step.

    *status* is either a ticket status string (e.g. "in-review",
    "human-gate") or the sentinel ``"retry"`` when the caller should
    re-invoke the adapter.

    *retry_reason* is set only when ``status == "retry"``.
    """

    status: str
    retry_reason: str | None = None

    @property
    def should_retry(self) -> bool:
        return self.status == "retry"

    @staticmethod
    def retry(reason: str = "unknown") -> PipelineOutcome:
        return PipelineOutcome(status="retry", retry_reason=reason)

    @staticmethod
    def terminal(status: str) -> PipelineOutcome:
        return PipelineOutcome(status=status)
