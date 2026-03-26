"""Shared mock adapters for tests."""

from __future__ import annotations

from pathlib import Path

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


class WorkspaceDiffAdapter(BaseAdapter):
    """Adapter that writes into ``RunRequest.working_directory``.

    Unlike :class:`DiffProducingAdapter` which hardcodes a repo path, this
    adapter writes into whatever directory the pipeline resolved — making it
    suitable for tests where workspace isolation redirects execution to an
    isolated worktree.
    """

    def __init__(self, filename="impl.txt", content="implemented\n"):
        self.filename = filename
        self.content = content
        self.calls: list[RunRequest] = []

    def execute(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        (Path(request.working_directory) / self.filename).write_text(self.content)
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="done",
            raw_stderr="",
            adapter_metadata={},
        )
