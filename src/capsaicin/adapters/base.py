"""Base adapter interface (T10)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from capsaicin.adapters.types import RunRequest, RunResult


class BaseAdapter(ABC):
    """Abstract base class for agent adapters."""

    @abstractmethod
    def execute(self, request: RunRequest) -> RunResult:
        """Execute an agent run and return the result."""
