"""Claude Code adapter (T12, T13).

Invokes Claude Code as an implementer or reviewer via subprocess.
"""

from __future__ import annotations

import json
import subprocess
import time

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import RunRequest, RunResult
from capsaicin.prompts import REVIEW_RESULT_SCHEMA
from capsaicin.validation import validate_review_result

# Envelope fields to extract into adapter_metadata.
_METADATA_KEYS = (
    "session_id",
    "num_turns",
    "total_cost_usd",
    "usage",
    "modelUsage",
    "permission_denials",
)


class ClaudeCodeAdapter(BaseAdapter):
    """Adapter for Claude Code CLI invocations."""

    def __init__(self, command: str = "claude") -> None:
        self.command = command

    def _build_command(self, request: RunRequest) -> list[str]:
        """Build the subprocess command list."""
        cmd = [
            self.command,
            "-p",
            "--output-format",
            "json",
        ]

        # Reviewer mode: add --json-schema and --allowed-tools
        if request.role == "reviewer":
            cmd.extend(
                [
                    "--json-schema",
                    json.dumps(REVIEW_RESULT_SCHEMA, separators=(",", ":")),
                ]
            )
            allowed_tools = request.adapter_config.get("allowed_tools", [])
            if allowed_tools:
                cmd.append("--allowed-tools")
                cmd.extend(allowed_tools)

        # Prompt must come after -- to avoid being parsed as flags/tool names
        cmd.append("--")
        cmd.append(request.prompt)
        return cmd

    @staticmethod
    def _extract_metadata(envelope: dict) -> dict:
        """Extract adapter_metadata fields from the Claude Code envelope."""
        meta = {}
        for key in _METADATA_KEYS:
            if key in envelope:
                meta[key] = envelope[key]
        return meta

    @staticmethod
    def _parse_envelope(stdout: str) -> dict | None:
        """Parse the outer JSON envelope from stdout.

        Returns None if stdout is not valid JSON or is not a JSON object.
        """
        try:
            parsed = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _extract_result_text(envelope: dict) -> str:
        """Extract the normalized assistant text from the envelope."""
        result = envelope.get("result")
        return result if isinstance(result, str) else ""

    def _handle_reviewer_result(
        self,
        request: RunRequest,
        envelope: dict,
        duration: float,
        raw_stdout: str,
        raw_stderr: str,
    ) -> RunResult:
        """Extract and validate structured reviewer output."""
        metadata = self._extract_metadata(envelope)
        result_text = self._extract_result_text(envelope)

        # Extract structured_output (primary) or fall back to result
        structured_raw = envelope.get("structured_output")
        if structured_raw is None:
            # Fall back: try parsing `result` as JSON
            result_field = envelope.get("result")
            if isinstance(result_field, str):
                try:
                    structured_raw = json.loads(result_field)
                except (json.JSONDecodeError, TypeError):
                    pass

        if not isinstance(structured_raw, dict):
            return RunResult(
                run_id=request.run_id,
                exit_status="parse_error",
                duration_seconds=duration,
                result_text=result_text,
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                adapter_metadata=metadata,
            )

        # Semantic validation via T17
        criteria_ids = [c.id for c in request.acceptance_criteria]
        validation = validate_review_result(structured_raw, criteria_ids)

        if not validation.is_valid:
            return RunResult(
                run_id=request.run_id,
                exit_status="parse_error",
                duration_seconds=duration,
                result_text=result_text,
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                adapter_metadata=metadata,
            )

        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=duration,
            result_text=result_text,
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
            structured_result=validation.result,
            adapter_metadata=metadata,
        )

    def execute(self, request: RunRequest) -> RunResult:
        """Execute a Claude Code run via subprocess."""
        cmd = self._build_command(request)
        start = time.monotonic()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                cwd=request.working_directory,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            return RunResult(
                run_id=request.run_id,
                exit_status="timeout",
                duration_seconds=duration,
                raw_stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                raw_stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            )

        duration = time.monotonic() - start
        raw_stdout = proc.stdout or ""
        raw_stderr = proc.stderr or ""

        # Non-zero exit code -> failure
        if proc.returncode != 0:
            envelope = self._parse_envelope(raw_stdout) or {}
            return RunResult(
                run_id=request.run_id,
                exit_status="failure",
                duration_seconds=duration,
                result_text=self._extract_result_text(envelope),
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                adapter_metadata=self._extract_metadata(envelope),
            )

        # Parse the JSON envelope
        envelope = self._parse_envelope(raw_stdout)
        if envelope is None:
            return RunResult(
                run_id=request.run_id,
                exit_status="failure",
                duration_seconds=duration,
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
            )

        # is_error: true -> failure regardless of exit code
        if envelope.get("is_error", False):
            return RunResult(
                run_id=request.run_id,
                exit_status="failure",
                duration_seconds=duration,
                result_text=self._extract_result_text(envelope),
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                adapter_metadata=self._extract_metadata(envelope),
            )

        # Reviewer mode: extract and validate structured output
        if request.role == "reviewer":
            return self._handle_reviewer_result(
                request,
                envelope,
                duration,
                raw_stdout,
                raw_stderr,
            )

        # Implementer mode: success
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=duration,
            result_text=self._extract_result_text(envelope),
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
            adapter_metadata=self._extract_metadata(envelope),
        )
