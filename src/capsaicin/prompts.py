"""Prompt assembly for implementer, reviewer, and planning runs (T11, T02, T07).

Builds structured prompts from ticket/planning context for adapter invocation.
"""

from __future__ import annotations

import json

from capsaicin.adapters.types import (
    AcceptanceCriterion,
    BackendEvidence,
    Finding,
    PlanningFinding,
)

# Full JSON Schema for the Review Result, used in reviewer prompts and
# passed to Claude Code via --json-schema in T13.
REVIEW_RESULT_SCHEMA: dict = {
    "type": "object",
    "required": ["verdict", "confidence", "findings", "scope_reviewed"],
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["pass", "fail", "escalate"],
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["severity", "category", "description", "disposition"],
                "additionalProperties": False,
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["blocking", "warning", "info"],
                    },
                    "category": {"type": "string"},
                    "location": {"type": ["string", "null"]},
                    "acceptance_criterion_id": {"type": ["string", "null"]},
                    "description": {"type": "string"},
                    "disposition": {
                        "type": "string",
                        "enum": ["open", "fixed", "wont_fix", "disputed"],
                    },
                },
            },
        },
        "scope_reviewed": {
            "type": "object",
            "required": ["files_examined", "tests_run", "criteria_checked"],
            "additionalProperties": False,
            "properties": {
                "files_examined": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "tests_run": {"type": "boolean"},
                "criteria_checked": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["criterion_id", "description"],
                        "additionalProperties": False,
                        "properties": {
                            "criterion_id": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


def _format_criteria(criteria: list[AcceptanceCriterion]) -> str:
    """Format acceptance criteria as a numbered list with statuses."""
    if not criteria:
        return "No acceptance criteria defined."
    lines = []
    for i, c in enumerate(criteria, 1):
        lines.append(f"{i}. [{c.status}] {c.description} (id: {c.id})")
    return "\n".join(lines)


def _format_findings(findings: list[Finding]) -> str:
    """Format findings as a bulleted list."""
    lines = []
    for f in findings:
        loc = f" at {f.location}" if f.location else ""
        crit = (
            f" (criterion: {f.acceptance_criterion_id})"
            if f.acceptance_criterion_id
            else ""
        )
        lines.append(
            f"- [{f.severity}] [{f.disposition}] {f.category}{loc}: {f.description}{crit}"
        )
    return "\n".join(lines)


def _format_evidence(evidence: list[BackendEvidence]) -> str:
    """Format backend validation evidence for inclusion in prompts.

    Each evidence type is rendered in its natural format:
    - commands as shell code blocks
    - outputs with labeled stdout/stderr and exit code
    - structured results as JSON code blocks
    - permission denials describe denied tool/action
    - behavioral notes as quoted prose
    """
    if not evidence:
        return ""

    parts: list[str] = []
    for ev in evidence:
        header = f"### {ev.title} ({ev.evidence_type.replace('_', ' ')})"
        parts.append(header)

        if ev.evidence_type == "command":
            if ev.command:
                parts.append(f"\n```bash\n{ev.command}\n```")
            if ev.body:
                parts.append(f"\n{ev.body}")

        elif ev.evidence_type == "output_envelope":
            if ev.command:
                parts.append(f"\nCommand: `{ev.command}`")
            if ev.stdout:
                parts.append(f"\n**stdout:**\n```\n{ev.stdout}\n```")
            if ev.stderr:
                parts.append(f"\n**stderr:**\n```\n{ev.stderr}\n```")
            if ev.body:
                parts.append(f"\n{ev.body}")

        elif ev.evidence_type in ("structured_result", "structured_result_sample"):
            if ev.structured_data:
                parts.append(
                    f"\n```json\n{json.dumps(ev.structured_data, indent=2)}\n```"
                )
            if ev.command:
                parts.append(f"\nCommand: `{ev.command}`")
            if ev.body:
                parts.append(f"\n{ev.body}")

        elif ev.evidence_type == "permission_denial":
            if ev.command:
                parts.append(f"\nDenied command: `{ev.command}`")
            if ev.body:
                parts.append(f"\n{ev.body}")
            if ev.stdout:
                parts.append(f"\n**stdout:**\n```\n{ev.stdout}\n```")
            if ev.stderr:
                parts.append(f"\n**stderr:**\n```\n{ev.stderr}\n```")

        elif ev.evidence_type == "behavioral_note":
            if ev.body:
                parts.append(f"\n> {ev.body}")

        else:
            # Fallback for unknown types
            if ev.body:
                parts.append(f"\n{ev.body}")
            if ev.command:
                parts.append(f"\nCommand: `{ev.command}`")

        parts.append("")

    return "\n".join(parts)


def build_implementer_prompt(
    ticket: dict,
    criteria: list[AcceptanceCriterion],
    prior_findings: list[Finding],
    cycle: int,
    max_cycles: int,
    evidence: list[BackendEvidence] | None = None,
    pending_evidence_descriptions: list[str] | None = None,
) -> str:
    """Build a prompt for an implementer run.

    Required elements (from cli.md:123-129):
    - ticket title and description
    - acceptance criteria with current statuses
    - prior open findings when revising
    - cycle number and max cycles
    - explicit implementer role instruction
    - explicit scope constraint

    Args:
        ticket: Dict with at least 'title' and 'description' keys.
        criteria: Acceptance criteria with current statuses.
        prior_findings: Open findings from prior review cycles.
        cycle: Current cycle number.
        max_cycles: Maximum allowed cycles.
    """
    parts = [
        "# Role",
        "",
        "You are an implementer agent. Your job is to make code and documentation "
        "changes that satisfy the ticket requirements and acceptance criteria below.",
        "",
        "# Scope Constraint",
        "",
        "Only make changes that are directly required by this ticket. Do not refactor "
        "unrelated code, add features beyond the scope, or modify files that are not "
        "relevant to the acceptance criteria.",
        "",
        "# Ticket",
        "",
        f"**Title**: {ticket['title']}",
        "",
        f"**Description**: {ticket['description']}",
        "",
        "# Acceptance Criteria",
        "",
        _format_criteria(criteria),
        "",
        f"# Cycle Information",
        "",
        f"This is cycle {cycle} of {max_cycles}.",
    ]

    if prior_findings:
        parts.extend(
            [
                "",
                "# Prior Findings",
                "",
                "The following findings were identified in a previous review cycle. "
                "Address all blocking findings:",
                "",
                _format_findings(prior_findings),
            ]
        )

    if evidence:
        parts.extend(
            [
                "",
                "# Backend Context",
                "",
                "The following backend validation evidence has been captured by the "
                "operator. Use this to understand observed backend behavior and "
                "ensure your implementation aligns with it:",
                "",
                _format_evidence(evidence),
            ]
        )

    if pending_evidence_descriptions:
        parts.extend(
            [
                "",
                "# Warning: Unresolved Evidence Requirements",
                "",
                "The parent epic has pending backend validation evidence requirements "
                "that have not yet been satisfied. The following behaviors are unverified. "
                "Proceed with caution and avoid relying on assumptions about backend "
                "behavior that is not backed by evidence:",
                "",
            ]
        )
        for desc in pending_evidence_descriptions:
            parts.append(f"- {desc}")

    return "\n".join(parts)


def build_reviewer_prompt(
    ticket: dict,
    criteria: list[AcceptanceCriterion],
    diff_context: str,
    prior_findings: list[Finding],
    evidence: list[BackendEvidence] | None = None,
    pending_evidence_descriptions: list[str] | None = None,
) -> str:
    """Build a prompt for a reviewer run.

    Required elements (from cli.md:163-169):
    - explicit independent reviewer role instruction
    - the captured diff being reviewed
    - ticket title, description, and acceptance criteria
    - prior findings with dispositions
    - explicit JSON schema-constrained output instruction
    - anti-bias instruction not to trust commit messages or inline rationale

    Args:
        ticket: Dict with at least 'title' and 'description' keys.
        criteria: Acceptance criteria with current statuses.
        diff_context: The git diff to review.
        prior_findings: Findings from prior review cycles with dispositions.
    """
    parts = [
        "# Role",
        "",
        "You are an independent code reviewer. Your job is to review the diff below "
        "against the ticket requirements and acceptance criteria. You must produce an "
        "honest, thorough assessment. You are not the implementer — you are a separate "
        "reviewer providing an independent quality gate.",
        "",
        "# Anti-Bias Instruction",
        "",
        "Do NOT trust commit messages, inline comments, or self-justifying rationale "
        "in the code as evidence that the implementation is correct. Evaluate the "
        "actual behavior and structure of the code independently.",
        "",
        "# Ticket",
        "",
        f"**Title**: {ticket['title']}",
        "",
        f"**Description**: {ticket['description']}",
        "",
        "# Acceptance Criteria",
        "",
        _format_criteria(criteria),
        "",
        "# Diff Under Review",
        "",
        "```diff",
        diff_context,
        "```",
    ]

    if prior_findings:
        parts.extend(
            [
                "",
                "# Prior Findings",
                "",
                "The following findings were identified in previous review cycles. "
                "Check whether they have been addressed:",
                "",
                _format_findings(prior_findings),
            ]
        )

    if evidence:
        parts.extend(
            [
                "",
                "# Backend Context",
                "",
                "The following backend validation evidence has been captured by the "
                "operator. Verify that the implementation aligns with observed "
                "backend behavior:",
                "",
                _format_evidence(evidence),
            ]
        )

    if pending_evidence_descriptions:
        parts.extend(
            [
                "",
                "# Warning: Unresolved Evidence Requirements",
                "",
                "The parent epic has pending backend validation evidence requirements "
                "that have not yet been satisfied. If the implementation relies on "
                "backend behavior that is not backed by evidence, emit a finding with "
                "category 'missing_evidence' to flag it. The following behaviors are "
                "unverified:",
                "",
            ]
        )
        for desc in pending_evidence_descriptions:
            parts.append(f"- {desc}")

    parts.extend(
        [
            "",
            "# Output Format",
            "",
            "You MUST respond with a JSON object conforming to this JSON Schema:",
            "",
            "```json",
            json.dumps(REVIEW_RESULT_SCHEMA, indent=2),
            "```",
            "",
            "Rules:",
            "- `verdict: fail` must include at least one finding with `severity: blocking`",
            "- `verdict: pass` cannot include any findings with `severity: blocking`",
            "- `verdict: escalate` means you cannot complete a reliable review without human input",
            "- Set `acceptance_criterion_id` on findings when they relate to a specific criterion",
            "- Include all criteria you checked in `criteria_checked`",
            "- List all files you examined in `files_examined`",
        ]
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Planning schemas (T02)
# ---------------------------------------------------------------------------

PLANNER_RESULT_SCHEMA: dict = {
    "type": "object",
    "required": ["epic", "tickets"],
    "additionalProperties": False,
    "properties": {
        "epic": {
            "type": "object",
            "required": ["title", "summary", "success_outcome"],
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "success_outcome": {"type": "string"},
            },
        },
        "tickets": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "sequence",
                    "title",
                    "goal",
                    "scope",
                    "non_goals",
                    "acceptance_criteria",
                    "dependencies",
                    "references",
                    "implementation_notes",
                ],
                "additionalProperties": False,
                "properties": {
                    "sequence": {"type": "integer", "minimum": 1},
                    "title": {"type": "string"},
                    "goal": {"type": "string"},
                    "scope": {"type": "array", "items": {"type": "string"}},
                    "non_goals": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["description"],
                            "additionalProperties": False,
                            "properties": {
                                "description": {"type": "string"},
                            },
                        },
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                    },
                    "references": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "implementation_notes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "sequencing_notes": {"type": ["string", "null"]},
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "suggested_evidence_requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["description", "suggested_command"],
                "additionalProperties": False,
                "properties": {
                    "description": {"type": "string"},
                    "suggested_command": {"type": "string"},
                },
            },
        },
    },
}

PLANNING_REVIEW_RESULT_SCHEMA: dict = {
    "type": "object",
    "required": ["verdict", "confidence", "findings", "scope_reviewed"],
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["pass", "fail", "escalate"],
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "severity",
                    "category",
                    "target_type",
                    "description",
                ],
                "additionalProperties": False,
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["blocking", "warning", "info"],
                    },
                    "category": {"type": "string"},
                    "target_type": {
                        "type": "string",
                        "enum": ["epic", "ticket"],
                    },
                    "target_sequence": {"type": ["integer", "null"]},
                    "description": {"type": "string"},
                    "disposition": {
                        "type": "string",
                        "enum": ["open", "fixed", "wont_fix", "disputed"],
                    },
                },
            },
        },
        "scope_reviewed": {
            "type": "object",
            "required": ["epic_reviewed", "tickets_reviewed"],
            "additionalProperties": False,
            "properties": {
                "epic_reviewed": {"type": "boolean"},
                "tickets_reviewed": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                },
                "aspects_checked": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Planning prompt helpers
# ---------------------------------------------------------------------------


def _format_planning_findings(findings: list[PlanningFinding]) -> str:
    """Format planning findings as a bulleted list."""
    lines = []
    for f in findings:
        target = f"[{f.target_type}]"
        if f.target_sequence is not None:
            target += f" (ticket #{f.target_sequence})"
        lines.append(
            f"- [{f.severity}] [{f.disposition}] {f.category} {target}: {f.description}"
        )
    return "\n".join(lines)


def _format_plan_draft(plan_draft: dict) -> str:
    """Format a current plan draft for inclusion in a revision prompt."""
    epic = plan_draft.get("epic", {})
    parts = [
        "## Epic",
        "",
        f"**Title**: {epic.get('title', '(untitled)')}",
        f"**Summary**: {epic.get('summary', '(none)')}",
        f"**Success Outcome**: {epic.get('success_outcome', '(none)')}",
        "",
        "## Tickets",
    ]

    for t in plan_draft.get("tickets", []):
        parts.append("")
        parts.append(f"### Ticket #{t['sequence']}: {t['title']}")
        parts.append(f"**Goal**: {t['goal']}")
        if t.get("scope"):
            parts.append("**Scope**: " + "; ".join(t["scope"]))
        if t.get("non_goals"):
            parts.append("**Non-goals**: " + "; ".join(t["non_goals"]))
        if t.get("acceptance_criteria"):
            parts.append("**Acceptance Criteria**:")
            for i, ac in enumerate(t["acceptance_criteria"], 1):
                desc = ac if isinstance(ac, str) else ac.get("description", "")
                parts.append(f"  {i}. {desc}")
        if t.get("dependencies"):
            parts.append(
                "**Dependencies**: " + ", ".join(f"#{d}" for d in t["dependencies"])
            )
        if t.get("references"):
            parts.append("**References**: " + ", ".join(t["references"]))
        if t.get("implementation_notes"):
            parts.append("**Implementation Notes**:")
            for note in t["implementation_notes"]:
                parts.append(f"  - {note}")

    if plan_draft.get("sequencing_notes"):
        parts.extend(["", "## Sequencing Notes", "", plan_draft["sequencing_notes"]])

    if plan_draft.get("open_questions"):
        parts.extend(["", "## Open Questions", ""])
        for q in plan_draft["open_questions"]:
            parts.append(f"- {q}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Planning prompt builders (T02)
# ---------------------------------------------------------------------------


def build_planner_draft_prompt(
    problem_statement: str,
    context_files: list[str] | None = None,
    evidence: list[BackendEvidence] | None = None,
) -> str:
    """Build a prompt for an initial planner draft run.

    Args:
        problem_statement: The problem to decompose into an epic and tickets.
        context_files: Optional list of file paths to reference.
        evidence: Backend validation evidence captured by the operator.
    """
    parts = [
        "# Role",
        "",
        "You are a planning agent. Your job is to decompose the problem statement "
        "below into a structured epic with implementation tickets. Each ticket must "
        "be self-contained enough for an independent implementation session.",
        "",
        "# Decomposition Constraints",
        "",
        "- Every ticket must have a clear goal, explicit scope, non-goals, at least "
        "one acceptance criterion, and implementation notes.",
        "- Use integer sequence numbers starting from 1 for tickets.",
        "- Dependencies reference other tickets by sequence number.",
        "- Dependencies must not form cycles.",
        "- Do not generate IDs — the orchestrator assigns them at persistence time.",
        "- References should list specific file paths or doc paths that the "
        "implementer must read.",
        "- When the epic involves a backend you have not seen evidence for, suggest "
        "evidence requirements with exact CLI commands the operator should run. "
        "Include these in the `suggested_evidence_requirements` array in your "
        "response.",
        "",
        "# Problem Statement",
        "",
        problem_statement,
    ]

    if evidence:
        parts.extend(
            [
                "",
                "# Backend Validation Evidence",
                "",
                "The following backend validation evidence has been captured by the "
                "operator. Ground your plan in this observed behavior rather than "
                "assumptions:",
                "",
                _format_evidence(evidence),
            ]
        )

    if context_files:
        parts.extend(
            [
                "",
                "# Context Files",
                "",
                "The following files are relevant to this problem:",
                "",
            ]
        )
        for cf in context_files:
            parts.append(f"- {cf}")

    parts.extend(
        [
            "",
            "# Output Format",
            "",
            "You MUST respond with a JSON object conforming to this JSON Schema:",
            "",
            "```json",
            json.dumps(PLANNER_RESULT_SCHEMA, indent=2),
            "```",
            "",
            "Return JSON only. Do not include prose before or after the JSON. "
            "Do not wrap the response in markdown fences.",
        ]
    )

    return "\n".join(parts)


def build_planner_revise_prompt(
    problem_statement: str,
    plan_draft: dict,
    prior_findings: list[PlanningFinding],
    cycle: int,
    max_cycles: int,
    context_files: list[str] | None = None,
    evidence: list[BackendEvidence] | None = None,
) -> str:
    """Build a prompt for a planner revision pass.

    Args:
        problem_statement: The original problem statement.
        plan_draft: The current plan draft as a dict (PlannerResult shape).
        prior_findings: Open findings from prior planning review cycles.
        cycle: Current cycle number.
        max_cycles: Maximum allowed cycles.
        context_files: Optional list of file paths to reference.
        evidence: Backend validation evidence captured by the operator.
    """
    parts = [
        "# Role",
        "",
        "You are a planning agent revising a plan based on reviewer feedback. "
        "Address all blocking findings while preserving aspects of the plan that "
        "were not flagged.",
        "",
        "# Decomposition Constraints",
        "",
        "- Every ticket must have a clear goal, explicit scope, non-goals, at least "
        "one acceptance criterion, and implementation notes.",
        "- Use integer sequence numbers starting from 1 for tickets.",
        "- Dependencies reference other tickets by sequence number.",
        "- Dependencies must not form cycles.",
        "- Do not generate IDs — the orchestrator assigns them at persistence time.",
        "- References should list specific file paths or doc paths that the "
        "implementer must read.",
        "- When the epic involves a backend you have not seen evidence for, suggest "
        "evidence requirements with exact CLI commands the operator should run. "
        "Include these in the `suggested_evidence_requirements` array in your "
        "response.",
        "",
        "# Problem Statement",
        "",
        problem_statement,
        "",
        "# Current Plan Draft",
        "",
        _format_plan_draft(plan_draft),
        "",
        "# Cycle Information",
        "",
        f"This is revision cycle {cycle} of {max_cycles}.",
    ]

    if prior_findings:
        parts.extend(
            [
                "",
                "# Prior Findings",
                "",
                "The following findings were identified in a previous planning "
                "review. Address all blocking findings:",
                "",
                _format_planning_findings(prior_findings),
            ]
        )

    if evidence:
        parts.extend(
            [
                "",
                "# Backend Validation Evidence",
                "",
                "The following backend validation evidence has been captured by the "
                "operator. Ground your revised plan in this observed behavior:",
                "",
                _format_evidence(evidence),
            ]
        )

    if context_files:
        parts.extend(
            [
                "",
                "# Context Files",
                "",
            ]
        )
        for cf in context_files:
            parts.append(f"- {cf}")

    parts.extend(
        [
            "",
            "# Output Format",
            "",
            "You MUST respond with a JSON object conforming to this JSON Schema:",
            "",
            "```json",
            json.dumps(PLANNER_RESULT_SCHEMA, indent=2),
            "```",
            "",
            "Return JSON only. Do not include prose before or after the JSON. "
            "Do not wrap the response in markdown fences.",
        ]
    )

    return "\n".join(parts)


def build_planning_reviewer_prompt(
    problem_statement: str,
    plan_draft: dict,
    prior_findings: list[PlanningFinding] | None = None,
    evidence: list[BackendEvidence] | None = None,
) -> str:
    """Build a prompt for a planning reviewer run.

    Args:
        problem_statement: The original problem statement.
        plan_draft: The current plan draft as a dict (PlannerResult shape).
        prior_findings: Findings from prior review cycles with dispositions.
        evidence: Backend validation evidence captured by the operator.
    """
    parts = [
        "# Role",
        "",
        "You are an independent planning reviewer. Your job is to review the "
        "plan below against the problem statement and assess whether the "
        "decomposition is sound, complete, and implementable. You are not the "
        "planner — you are a separate reviewer providing an independent "
        "quality gate.",
        "",
        "# Anti-Bias Instruction",
        "",
        "Do NOT assume the plan is correct because it appears well-structured. "
        "Evaluate whether the decomposition actually covers the problem, whether "
        "dependencies are accurate, whether scope boundaries are clear, and "
        "whether implementation notes provide sufficient context for a fresh "
        "coding session.",
        "",
        "# Problem Statement",
        "",
        problem_statement,
        "",
        "# Plan Under Review",
        "",
        _format_plan_draft(plan_draft),
    ]

    if prior_findings:
        parts.extend(
            [
                "",
                "# Prior Findings",
                "",
                "The following findings were identified in previous review cycles. "
                "Check whether they have been addressed:",
                "",
                _format_planning_findings(prior_findings),
            ]
        )

    if evidence:
        parts.extend(
            [
                "",
                "# Backend Validation Evidence",
                "",
                "The following backend validation evidence has been captured by the "
                "operator. Verify that the plan references and aligns with this "
                "observed behavior:",
                "",
                _format_evidence(evidence),
            ]
        )

    parts.extend(
        [
            "",
            "# Output Format",
            "",
            "You MUST respond with a JSON object conforming to this JSON Schema:",
            "",
            "```json",
            json.dumps(PLANNING_REVIEW_RESULT_SCHEMA, indent=2),
            "```",
            "",
            "Return JSON only. Do not include prose before or after the JSON. "
            "Do not wrap the response in markdown fences.",
            "",
            "Rules:",
            "- `verdict: fail` must include at least one finding with "
            "`severity: blocking`",
            "- `verdict: pass` cannot include any findings with `severity: blocking`",
            "- `verdict: escalate` means you cannot complete a reliable review "
            "without human input",
            '- Set `target_type: "epic"` with `target_sequence: null` for '
            "epic-level findings",
            '- Set `target_type: "ticket"` with `target_sequence` set to the '
            "ticket's sequence number for ticket-level findings",
            "- Include all ticket sequence numbers you reviewed in `tickets_reviewed`",
            "- Set `epic_reviewed: true` if you reviewed the epic-level artifacts",
            "- If the plan references backend behaviors that are not backed by supplied "
            "evidence, emit a finding with `category: 'missing_evidence'` to flag it",
        ]
    )

    return "\n".join(parts)
