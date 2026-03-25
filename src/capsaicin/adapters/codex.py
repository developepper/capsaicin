"""OpenAI Codex CLI adapter (T06).

Invokes ``codex exec`` as a non-interactive subprocess, parses the JSONL
event stream produced by ``--json`` mode, and maps the output to the
shared RunRequest/RunResult contract.

Key differences from the Claude Code adapter:

- stdout is **JSONL** (one JSON object per line), not a single JSON envelope.
- The final assistant text lives in an ``item.completed`` event where
  ``item.type == "agent_message"`` and the text is in ``item.text``.
- Token usage metadata arrives in a separate ``turn.completed`` event.
- ``codex exec`` with ``--sandbox read-only`` does **not** emit a distinct
  machine-readable permission-denied signal; refusals appear as ordinary
  assistant text and the process may still exit 0.
- ``--output-schema`` requires stricter object schemas than the shared
  adapter contracts. In practice every declared object property must be listed
  in ``required`` and object schemas must set
  ``"additionalProperties": false``.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from copy import deepcopy
from pathlib import Path

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

# Phrases that indicate a read-only sandbox write refusal in assistant text.
_PERMISSION_DENIAL_PATTERNS = [
    re.compile(r"operation not permitted", re.IGNORECASE),
    re.compile(r"read[- ]?only file ?system", re.IGNORECASE),
    re.compile(r"(?:file ?system|filesystem) is read[- ]?only", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
]


class CodexAdapter(BaseAdapter):
    """Adapter for OpenAI Codex CLI invocations via ``codex exec``."""

    def __init__(self, command: str = "codex") -> None:
        self.command = command

    # ------------------------------------------------------------------
    # Command construction
    # ------------------------------------------------------------------

    @staticmethod
    def _structured_output_kind(request: RunRequest) -> str | None:
        """Return the structured output kind requested for this run."""
        kind = request.adapter_config.get("structured_output")
        if isinstance(kind, str):
            return kind
        if request.role == "reviewer":
            return "review"
        return None

    @staticmethod
    def _structured_output_schema(kind: str) -> dict | None:
        """Return the JSON schema for *kind*, or None."""
        if kind == "planner":
            return PLANNER_RESULT_SCHEMA
        if kind == "planning_review":
            return PLANNING_REVIEW_RESULT_SCHEMA
        if kind == "review":
            return REVIEW_RESULT_SCHEMA
        return None

    @staticmethod
    def _normalize_schema_for_codex(schema: dict) -> dict:
        """Normalize a shared JSON schema for Codex ``--output-schema``.

        Codex enforces stricter object-schema rules than the shared adapter
        contracts use. Every object property must be present in ``required``,
        including fields that the workflow treats as optional at the semantic
        layer, and object schemas must disable additional properties.
        """

        def _normalize(node: object) -> object:
            if isinstance(node, list):
                return [_normalize(item) for item in node]
            if not isinstance(node, dict):
                return node

            normalized = {key: _normalize(value) for key, value in node.items()}

            if normalized.get("type") == "object":
                properties = normalized.get("properties")
                if isinstance(properties, dict):
                    normalized["required"] = list(properties.keys())
                normalized["additionalProperties"] = False

            items = normalized.get("items")
            if items is not None:
                normalized["items"] = _normalize(items)

            return normalized

        return _normalize(deepcopy(schema))

    def _build_command(
        self,
        request: RunRequest,
        schema_path: str | None = None,
    ) -> list[str]:
        """Build the subprocess command list for ``codex exec``."""
        cmd = [
            self.command,
            "exec",
            "--json",
            "--ephemeral",
        ]

        # Sandbox mode
        if request.mode == "read-only":
            cmd.extend(["--sandbox", "read-only"])

        # Model override
        model = request.adapter_config.get("model")
        if model:
            cmd.extend(["--model", model])

        # Output schema file
        if schema_path:
            cmd.extend(["--output-schema", schema_path])

        # Working directory
        cmd.extend(["--cd", request.working_directory])

        # Prompt comes last
        cmd.append(request.prompt)
        return cmd

    # ------------------------------------------------------------------
    # JSONL event stream parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_jsonl_events(stdout: str) -> list[dict]:
        """Parse JSONL stdout into a list of event dicts."""
        events: list[dict] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    @staticmethod
    def _extract_agent_text(events: list[dict]) -> str:
        """Extract the final assistant text from JSONL events.

        Looks for ``item.completed`` events where ``item.type == "agent_message"``
        and concatenates their ``item.text`` fields.
        """
        texts: list[str] = []
        for ev in events:
            if ev.get("type") != "item.completed":
                continue
            item = ev.get("item", {})
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    texts.append(text)
        return "\n".join(texts)

    @staticmethod
    def _extract_usage_metadata(events: list[dict]) -> dict:
        """Extract token usage from ``turn.completed`` events."""
        meta: dict = {}
        for ev in events:
            if ev.get("type") == "turn.completed":
                usage = ev.get("usage")
                if isinstance(usage, dict):
                    meta["usage"] = usage
        return meta

    @staticmethod
    def _detect_error_event(events: list[dict]) -> str | None:
        """Return the error message from a JSONL ``error`` event, if any."""
        for ev in events:
            if ev.get("type") == "error":
                error = ev.get("error", {})
                if isinstance(error, dict):
                    return error.get("message", str(error))
                return str(error)
        return None

    @staticmethod
    def _detect_turn_failed(events: list[dict]) -> str | None:
        """Return error info from ``turn.failed`` events."""
        for ev in events:
            if ev.get("type") == "turn.failed":
                error = ev.get("error", {})
                if isinstance(error, dict):
                    code = error.get("code", "")
                    msg = error.get("message", "")
                    return f"{code}: {msg}" if code else msg
                return str(error)
        return None

    # ------------------------------------------------------------------
    # Structured output extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json_from_text(text: str) -> dict | None:
        """Best-effort JSON extraction from assistant text."""
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
            try:
                parsed = json.loads(bare.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    # ------------------------------------------------------------------
    # Permission denial heuristic
    # ------------------------------------------------------------------

    @staticmethod
    def _text_indicates_permission_denial(text: str) -> bool:
        """Return True if assistant text contains permission-denial language."""
        for pat in _PERMISSION_DENIAL_PATTERNS:
            if pat.search(text):
                return True
        return False

    # ------------------------------------------------------------------
    # Structured result parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_review_result(
        request: RunRequest,
        structured_raw: dict,
    ) -> ReviewResult | None:
        criteria_ids = [c.id for c in request.acceptance_criteria]
        validation = validate_review_result(structured_raw, criteria_ids)
        if not validation.is_valid:
            return None
        result = validation.result
        return result if isinstance(result, ReviewResult) else None

    @staticmethod
    def _parse_planner_result(structured_raw: dict) -> PlannerResult | None:
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
        valid_sequences = request.adapter_config.get("valid_sequences", [])
        validation = validate_planning_review_result(structured_raw, valid_sequences)
        if not validation.is_valid:
            return None
        result = validation.result
        return result if isinstance(result, PlanningReviewResult) else None

    def _handle_structured_result(
        self,
        request: RunRequest,
        result_text: str,
        structured_raw: dict | None,
        duration: float,
        raw_stdout: str,
        raw_stderr: str,
        metadata: dict,
    ) -> RunResult:
        """Extract and validate structured output for review/planning runs."""
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

    # ------------------------------------------------------------------
    # Main execute
    # ------------------------------------------------------------------

    def execute(self, request: RunRequest) -> RunResult:
        """Execute a Codex CLI run via ``codex exec --json``."""
        kind = self._structured_output_kind(request)
        schema_path: str | None = None
        schema_tmpfile = None

        # Write output schema to a temp file if structured output is needed.
        if kind is not None:
            schema = self._structured_output_schema(kind)
            if schema is not None:
                schema = self._normalize_schema_for_codex(schema)
                schema_tmpfile = tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".json",
                    prefix="codex-schema-",
                    delete=False,
                )
                json.dump(schema, schema_tmpfile, separators=(",", ":"))
                schema_tmpfile.close()
                schema_path = schema_tmpfile.name

        cmd = self._build_command(request, schema_path=schema_path)
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
        finally:
            if schema_tmpfile is not None:
                Path(schema_tmpfile.name).unlink(missing_ok=True)

        duration = time.monotonic() - start
        raw_stdout = proc.stdout or ""
        raw_stderr = proc.stderr or ""

        # Parse the JSONL event stream.
        events = self._parse_jsonl_events(raw_stdout)
        result_text = self._extract_agent_text(events)
        metadata = self._extract_usage_metadata(events)

        # Check for structured error events.
        turn_failed = self._detect_turn_failed(events)
        error_event = self._detect_error_event(events)

        # Non-zero exit code -> failure.
        if proc.returncode != 0:
            error_detail = turn_failed or error_event or ""
            if error_detail:
                metadata["error_detail"] = error_detail
            return RunResult(
                run_id=request.run_id,
                exit_status="failure",
                duration_seconds=duration,
                result_text=result_text,
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                adapter_metadata=metadata,
            )

        # Structured output mode — parse structured result first so that
        # reviewer/planner results whose text happens to mention "permission
        # denied" (e.g. as an application-level finding) are not misclassified.
        if kind is not None:
            structured_raw = self._extract_json_from_text(result_text)
            return self._handle_structured_result(
                request,
                result_text,
                structured_raw,
                duration,
                raw_stdout,
                raw_stderr,
                metadata,
            )

        # Permission denial heuristic (implementer / plain mode only):
        # Codex does not emit a distinct machine-readable signal for
        # read-only sandbox refusals — they appear as ordinary assistant
        # text and the process exits 0.
        if result_text and self._text_indicates_permission_denial(result_text):
            return RunResult(
                run_id=request.run_id,
                exit_status="permission_denied",
                duration_seconds=duration,
                result_text=result_text,
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                adapter_metadata=metadata,
            )

        # Implementer / plain mode: success.
        return RunResult(
            run_id=request.run_id,
            exit_status="success",
            duration_seconds=duration,
            result_text=result_text,
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
            adapter_metadata=metadata,
        )
