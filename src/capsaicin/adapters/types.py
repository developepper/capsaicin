"""Adapter contract types (T10).

Dataclasses for RunRequest, RunResult, ReviewResult, and supporting types.
All types support JSON round-trip via to_dict/from_dict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Enum constants
# ---------------------------------------------------------------------------

VALID_ROLES = frozenset({"implementer", "reviewer", "planner", "human"})
VALID_MODES = frozenset({"read-write", "read-only"})
VALID_VERDICTS = frozenset({"pass", "fail", "escalate"})
VALID_CONFIDENCES = frozenset({"high", "medium", "low"})
VALID_SEVERITIES = frozenset({"blocking", "warning", "info"})
VALID_DISPOSITIONS = frozenset({"open", "fixed", "wont_fix", "disputed"})
VALID_TARGET_TYPES = frozenset({"epic", "ticket"})
VALID_EXIT_STATUSES = frozenset(
    {
        "success",
        "failure",
        "timeout",
        "contract_violation",
        "parse_error",
        "permission_denied",
    }
)
VALID_CRITERION_STATUSES = frozenset({"pending", "met", "unmet", "disputed"})


def _check_enum(value: str, valid: frozenset[str], field_name: str) -> None:
    """Raise ValueError if value is not in the valid set."""
    if value not in valid:
        raise ValueError(
            f"Invalid {field_name}: '{value}'. Must be one of: {sorted(valid)}"
        )


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


@dataclass
class AcceptanceCriterion:
    """Acceptance criterion context for run requests."""

    id: str
    description: str
    status: str = "pending"

    def __post_init__(self) -> None:
        _check_enum(self.status, VALID_CRITERION_STATUSES, "status")

    def to_dict(self) -> dict:
        return {"id": self.id, "description": self.description, "status": self.status}

    @classmethod
    def from_dict(cls, data: dict) -> AcceptanceCriterion:
        return cls(
            id=data["id"],
            description=data["description"],
            status=data.get("status", "pending"),
        )


@dataclass
class CriterionChecked:
    """A criterion that the reviewer checked."""

    criterion_id: str
    description: str

    def to_dict(self) -> dict:
        return {"criterion_id": self.criterion_id, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> CriterionChecked:
        return cls(criterion_id=data["criterion_id"], description=data["description"])


@dataclass
class ScopeReviewed:
    """Evidence of what the reviewer actually checked."""

    files_examined: list[str] = field(default_factory=list)
    tests_run: bool = False
    criteria_checked: list[CriterionChecked] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files_examined": self.files_examined,
            "tests_run": self.tests_run,
            "criteria_checked": [c.to_dict() for c in self.criteria_checked],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ScopeReviewed:
        return cls(
            files_examined=data.get("files_examined", []),
            tests_run=data.get("tests_run", False),
            criteria_checked=[
                CriterionChecked.from_dict(c) for c in data.get("criteria_checked", [])
            ],
        )


@dataclass
class Finding:
    """A review finding."""

    severity: str
    category: str
    description: str
    location: str | None = None
    acceptance_criterion_id: str | None = None
    disposition: str = "open"

    def __post_init__(self) -> None:
        _check_enum(self.severity, VALID_SEVERITIES, "severity")
        _check_enum(self.disposition, VALID_DISPOSITIONS, "disposition")

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
            "location": self.location,
            "acceptance_criterion_id": self.acceptance_criterion_id,
            "disposition": self.disposition,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Finding:
        return cls(
            severity=data["severity"],
            category=data["category"],
            description=data["description"],
            location=data.get("location"),
            acceptance_criterion_id=data.get("acceptance_criterion_id"),
            disposition=data.get("disposition", "open"),
        )


# ---------------------------------------------------------------------------
# Review result
# ---------------------------------------------------------------------------


@dataclass
class ReviewResult:
    """Structured result from a reviewer run."""

    verdict: str
    confidence: str
    findings: list[Finding] = field(default_factory=list)
    scope_reviewed: ScopeReviewed = field(default_factory=ScopeReviewed)

    def __post_init__(self) -> None:
        _check_enum(self.verdict, VALID_VERDICTS, "verdict")
        _check_enum(self.confidence, VALID_CONFIDENCES, "confidence")

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "findings": [f.to_dict() for f in self.findings],
            "scope_reviewed": self.scope_reviewed.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ReviewResult:
        return cls(
            verdict=data["verdict"],
            confidence=data["confidence"],
            findings=[Finding.from_dict(f) for f in data.get("findings", [])],
            scope_reviewed=ScopeReviewed.from_dict(data.get("scope_reviewed", {})),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> ReviewResult:
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# Planning types
# ---------------------------------------------------------------------------


@dataclass
class PlanningFinding:
    """A planning review finding targeting an epic or ticket artifact."""

    severity: str
    category: str
    description: str
    target_type: str
    target_sequence: int | None = None
    disposition: str = "open"

    def __post_init__(self) -> None:
        _check_enum(self.severity, VALID_SEVERITIES, "severity")
        _check_enum(self.target_type, VALID_TARGET_TYPES, "target_type")
        _check_enum(self.disposition, VALID_DISPOSITIONS, "disposition")

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
            "target_type": self.target_type,
            "target_sequence": self.target_sequence,
            "disposition": self.disposition,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanningFinding:
        return cls(
            severity=data["severity"],
            category=data["category"],
            description=data["description"],
            target_type=data["target_type"],
            target_sequence=data.get("target_sequence"),
            disposition=data.get("disposition", "open"),
        )


@dataclass
class PlanningScopeReviewed:
    """Evidence of what the planning reviewer actually checked."""

    epic_reviewed: bool = False
    tickets_reviewed: list[int] = field(default_factory=list)
    aspects_checked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "epic_reviewed": self.epic_reviewed,
            "tickets_reviewed": self.tickets_reviewed,
            "aspects_checked": self.aspects_checked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanningScopeReviewed:
        return cls(
            epic_reviewed=data.get("epic_reviewed", False),
            tickets_reviewed=data.get("tickets_reviewed", []),
            aspects_checked=data.get("aspects_checked", []),
        )


@dataclass
class PlanningReviewResult:
    """Structured result from a planning reviewer run."""

    verdict: str
    confidence: str
    findings: list[PlanningFinding] = field(default_factory=list)
    scope_reviewed: PlanningScopeReviewed = field(
        default_factory=PlanningScopeReviewed
    )

    def __post_init__(self) -> None:
        _check_enum(self.verdict, VALID_VERDICTS, "verdict")
        _check_enum(self.confidence, VALID_CONFIDENCES, "confidence")

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "findings": [f.to_dict() for f in self.findings],
            "scope_reviewed": self.scope_reviewed.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanningReviewResult:
        return cls(
            verdict=data["verdict"],
            confidence=data["confidence"],
            findings=[
                PlanningFinding.from_dict(f) for f in data.get("findings", [])
            ],
            scope_reviewed=PlanningScopeReviewed.from_dict(
                data.get("scope_reviewed", {})
            ),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> PlanningReviewResult:
        return cls.from_dict(json.loads(s))


@dataclass
class PlannedAcceptanceCriterion:
    """An acceptance criterion within a planned ticket."""

    description: str

    def to_dict(self) -> dict:
        return {"description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> PlannedAcceptanceCriterion:
        return cls(description=data["description"])


@dataclass
class PlannedTicketData:
    """A ticket within a planner result."""

    sequence: int
    title: str
    goal: str
    scope: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    acceptance_criteria: list[PlannedAcceptanceCriterion] = field(
        default_factory=list
    )
    dependencies: list[int] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    implementation_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "title": self.title,
            "goal": self.goal,
            "scope": self.scope,
            "non_goals": self.non_goals,
            "acceptance_criteria": [c.to_dict() for c in self.acceptance_criteria],
            "dependencies": self.dependencies,
            "references": self.references,
            "implementation_notes": self.implementation_notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlannedTicketData:
        return cls(
            sequence=data["sequence"],
            title=data["title"],
            goal=data["goal"],
            scope=data.get("scope", []),
            non_goals=data.get("non_goals", []),
            acceptance_criteria=[
                PlannedAcceptanceCriterion.from_dict(c)
                for c in data.get("acceptance_criteria", [])
            ],
            dependencies=data.get("dependencies", []),
            references=data.get("references", []),
            implementation_notes=data.get("implementation_notes", []),
        )


@dataclass
class PlannedEpicData:
    """The epic portion of a planner result."""

    title: str
    summary: str
    success_outcome: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "success_outcome": self.success_outcome,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlannedEpicData:
        return cls(
            title=data["title"],
            summary=data["summary"],
            success_outcome=data["success_outcome"],
        )


@dataclass
class PlannerResult:
    """Structured result from a planner run."""

    epic: PlannedEpicData
    tickets: list[PlannedTicketData]
    sequencing_notes: str | None = None
    open_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "epic": self.epic.to_dict(),
            "tickets": [t.to_dict() for t in self.tickets],
            "sequencing_notes": self.sequencing_notes,
            "open_questions": self.open_questions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlannerResult:
        return cls(
            epic=PlannedEpicData.from_dict(data["epic"]),
            tickets=[PlannedTicketData.from_dict(t) for t in data["tickets"]],
            sequencing_notes=data.get("sequencing_notes"),
            open_questions=data.get("open_questions", []),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> PlannerResult:
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# Run request and result
# ---------------------------------------------------------------------------


@dataclass
class RunRequest:
    """Request envelope for an adapter invocation."""

    run_id: str
    role: str
    mode: str
    working_directory: str
    prompt: str
    diff_context: str | None = None
    context_files: list[str] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)
    prior_findings: list[Finding] = field(default_factory=list)
    timeout_seconds: int = 300
    adapter_config: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_enum(self.role, VALID_ROLES, "role")
        _check_enum(self.mode, VALID_MODES, "mode")

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "role": self.role,
            "mode": self.mode,
            "working_directory": self.working_directory,
            "prompt": self.prompt,
            "diff_context": self.diff_context,
            "context_files": self.context_files,
            "acceptance_criteria": [c.to_dict() for c in self.acceptance_criteria],
            "prior_findings": [f.to_dict() for f in self.prior_findings],
            "timeout_seconds": self.timeout_seconds,
            "adapter_config": self.adapter_config,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RunRequest:
        return cls(
            run_id=data["run_id"],
            role=data["role"],
            mode=data["mode"],
            working_directory=data["working_directory"],
            prompt=data["prompt"],
            diff_context=data.get("diff_context"),
            context_files=data.get("context_files", []),
            acceptance_criteria=[
                AcceptanceCriterion.from_dict(c)
                for c in data.get("acceptance_criteria", [])
            ],
            prior_findings=[
                Finding.from_dict(f) for f in data.get("prior_findings", [])
            ],
            timeout_seconds=data.get("timeout_seconds", 300),
            adapter_config=data.get("adapter_config", {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> RunRequest:
        return cls.from_dict(json.loads(s))


_StructuredResult = ReviewResult | PlannerResult | PlanningReviewResult


def _serialize_structured_result(sr: _StructuredResult | None) -> dict | None:
    """Serialize a structured result with a _result_type discriminator."""
    if sr is None:
        return None
    d = sr.to_dict()
    if isinstance(sr, PlannerResult):
        d["_result_type"] = "planner"
    elif isinstance(sr, PlanningReviewResult):
        d["_result_type"] = "planning_review"
    else:
        d["_result_type"] = "review"
    return d


def _deserialize_structured_result(data: dict | None) -> _StructuredResult | None:
    """Deserialize a structured result using the _result_type discriminator."""
    if data is None:
        return None
    result_type = data.get("_result_type", "review")
    if result_type == "planner":
        return PlannerResult.from_dict(data)
    if result_type == "planning_review":
        return PlanningReviewResult.from_dict(data)
    return ReviewResult.from_dict(data)


@dataclass
class RunResult:
    """Result envelope from an adapter invocation."""

    run_id: str
    exit_status: str
    duration_seconds: float = 0.0
    result_text: str = ""
    raw_stdout: str = ""
    raw_stderr: str = ""
    structured_result: _StructuredResult | None = None
    adapter_metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_enum(self.exit_status, VALID_EXIT_STATUSES, "exit_status")

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "exit_status": self.exit_status,
            "duration_seconds": self.duration_seconds,
            "result_text": self.result_text,
            "raw_stdout": self.raw_stdout,
            "raw_stderr": self.raw_stderr,
            "structured_result": _serialize_structured_result(
                self.structured_result
            ),
            "adapter_metadata": self.adapter_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RunResult:
        sr = data.get("structured_result")
        return cls(
            run_id=data["run_id"],
            exit_status=data["exit_status"],
            duration_seconds=data.get("duration_seconds", 0.0),
            result_text=data.get("result_text", ""),
            raw_stdout=data.get("raw_stdout", ""),
            raw_stderr=data.get("raw_stderr", ""),
            structured_result=_deserialize_structured_result(sr),
            adapter_metadata=data.get("adapter_metadata", {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> RunResult:
        return cls.from_dict(json.loads(s))
