"""Shared mock adapters for tests."""

from __future__ import annotations

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import RunRequest, RunResult


class DiffProducingAdapter(BaseAdapter):
    """Adapter that modifies a file in the repo before returning success."""

    def __init__(self, repo_path, filename="impl.txt", content="implemented\n"):
        self.repo_path = repo_path
        self.filename = filename
        self.content = content
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        (self.repo_path / self.filename).write_text(self.content)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            adapter_metadata={},
        )
