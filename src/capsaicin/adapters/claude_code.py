"""Claude Code adapter (T12, T13, T01).

Invokes Claude Code as an implementer or reviewer via subprocess.
Detects permission denials and classifies them as a distinct run outcome.
"""

from __future__ import annotations

import json
import re
import subprocess
import time

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import (
    PlannerResult,
    PlanningReviewResult,
    ReviewResult,
    RunRequest,
    RunResult,
)
from capsaicin.prompts import (
    PLANNER_RESULT_SCHEMA,
    PLANNING_REVIEW_RESULT_SCHEMA,
    REVIEW_RESULT_SCHEMA,
)
from capsaicin.validation import (
    validate_planner_result,
    validate_planning_review_result,
    validate_review_result,
)

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

        schema = self._structured_output_schema(request)
        if schema is not None:
            cmd.extend(
                [
                    "--json-schema",
                    json.dumps(schema, separators=(",", ":")),
                ]
            )

        # Reviewer mode: add --allowed-tools
        if request.role == "reviewer":
            allowed_tools = request.adapter_config.get("allowed_tools", [])
            if allowed_tools:
                cmd.append("--allowed-tools")
                cmd.extend(allowed_tools)

        # Prompt must come after -- to avoid being parsed as flags/tool names
        cmd.append("--")
        cmd.append(request.prompt)
        return cmd

    @staticmethod
    def _structured_output_kind(request: RunRequest) -> str | None:
        """Return the structured output kind requested for this run."""
        kind = request.adapter_config.get("structured_output")
        if isinstance(kind, str):
            return kind
        if request.role == "reviewer":
            return "review"
        return None

    def _structured_output_schema(self, request: RunRequest) -> dict | None:
        """Return the JSON schema for the given run request, if any."""
        kind = self._structured_output_kind(request)
        if kind == "planner":
            return PLANNER_RESULT_SCHEMA
        if kind == "planning_review":
            return PLANNING_REVIEW_RESULT_SCHEMA
        if kind == "review":
            return REVIEW_RESULT_SCHEMA
        return None

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

    @staticmethod
    def _extract_json_from_text(text: str) -> dict | None:
        """Best-effort JSON extraction from raw result text."""
        if not text:
            return None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            try:
                parsed = json.loads(fenced.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass

        bare = re.search(r"(\{.*\})", text, re.DOTALL)
        if bare:
            candidate = bare.group(1)
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def _extract_structured_output(self, envelope: dict) -> dict | None:
        """Extract structured output from a Claude envelope."""
        structured_raw = envelope.get("structured_output")
        if isinstance(structured_raw, dict):
            return structured_raw
        return self._extract_json_from_text(self._extract_result_text(envelope))

    @staticmethod
    def _has_permission_denials(envelope: dict) -> bool:
        """Return True if the envelope contains one or more structured permission denials.

        Only triggers on the structured dict format observed in real Claude Code
        envelopes (each entry has ``tool_name``, ``tool_use_id``, ``tool_input``).
        Simple string lists like ``["Write"]`` are ignored.
        """
        denials = envelope.get("permission_denials")
        if not isinstance(denials, list) or len(denials) == 0:
            return False
        return any(isinstance(d, dict) for d in denials)

    @staticmethod
    def _normalize_denials(raw_denials: list[dict]) -> list[dict]:
        """Normalize raw permission denial entries into a stable internal shape.

        Each normalized entry contains:
        - tool_name
        - tool_use_id
        - file_path (for Edit and Write, when present)
        - command (for Bash, when present)
        """
        normalized = []
        for denial in raw_denials:
            if not isinstance(denial, dict):
                continue
            entry: dict = {
                "tool_name": denial.get("tool_name", ""),
                "tool_use_id": denial.get("tool_use_id", ""),
            }
            tool_input = denial.get("tool_input", {})
            tool_name = entry["tool_name"]
            if tool_name in ("Edit", "Write") and "file_path" in tool_input:
                entry["file_path"] = tool_input["file_path"]
            if tool_name == "Bash" and "command" in tool_input:
                entry["command"] = tool_input["command"]
            normalized.append(entry)
        return normalized

    def _parse_review_result(
        self,
        request: RunRequest,
        structured_raw: dict,
    ) -> ReviewResult | None:
        """Validate and materialize a code review result."""
        criteria_ids = [c.id for c in request.acceptance_criteria]
        validation = validate_review_result(structured_raw, criteria_ids)
        if not validation.is_valid:
            return None
        result = validation.result
        return result if isinstance(result, ReviewResult) else None

    @staticmethod
    def _parse_planner_result(structured_raw: dict) -> PlannerResult | None:
        """Validate and materialize a planner result."""
        validation = validate_planner_result(structured_raw)
        if not validation.is_valid:
            return None
        result = validation.result
        return result if isinstance(result, PlannerResult) else None

    @staticmethod
    def _parse_planning_review_result(
        request: RunRequest,
        structured_raw: dict,
    ) -> PlanningReviewResult | None:
        """Validate and materialize a planning review result."""
        valid_sequences = request.adapter_config.get("valid_sequences", [])
        validation = validate_planning_review_result(structured_raw, valid_sequences)
        if not validation.is_valid:
            return None
        result = validation.result
        return result if isinstance(result, PlanningReviewResult) else None

    def _handle_structured_result(
        self,
        request: RunRequest,
        envelope: dict,
        duration: float,
        raw_stdout: str,
        raw_stderr: str,
    ) -> RunResult:
        """Extract and validate structured output for review/planning runs."""
        metadata = self._extract_metadata(envelope)
        result_text = self._extract_result_text(envelope)
        structured_raw = self._extract_structured_output(envelope)
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

        kind = self._structured_output_kind(request)
        if kind == "planner":
            structured_result = self._parse_planner_result(structured_raw)
        elif kind == "planning_review":
            structured_result = self._parse_planning_review_result(
                request, structured_raw
            )
        else:
            structured_result = self._parse_review_result(request, structured_raw)

        if structured_result is None:
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
            structured_result=structured_result,
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

        # Parse the JSON envelope early so permission denials can be
        # detected even when the process exited non-zero or is_error is set.
        envelope = self._parse_envelope(raw_stdout)

        # Permission denials take precedence over other error signals.
        # Real failing runs still returned is_error: false and a clean
        # exit, but future Claude versions or edge cases may combine
        # denials with non-zero exit or is_error: true.
        if envelope is not None and self._has_permission_denials(envelope):
            metadata = self._extract_metadata(envelope)
            raw_denials = envelope.get("permission_denials", [])
            metadata["normalized_denials"] = self._normalize_denials(raw_denials)
            return RunResult(
                run_id=request.run_id,
                exit_status="permission_denied",
                duration_seconds=duration,
                result_text=self._extract_result_text(envelope),
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                adapter_metadata=metadata,
            )

        # Non-zero exit code -> failure
        if proc.returncode != 0:
            meta_env = envelope or {}
            return RunResult(
                run_id=request.run_id,
                exit_status="failure",
                duration_seconds=duration,
                result_text=self._extract_result_text(meta_env),
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                adapter_metadata=self._extract_metadata(meta_env),
            )

        # Unparseable stdout -> failure
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

        # Structured-output modes: extract and validate structured output
        if self._structured_output_kind(request) is not None:
            return self._handle_structured_result(
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
