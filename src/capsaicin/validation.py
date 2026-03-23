"""Review and planning result validation (T17, T02).

Pure validation of raw result dicts against semantic rules.
No DB access, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from capsaicin.adapters.types import (
    PlannerResult,
    PlanningReviewResult,
    ReviewResult,
)

# Top-level fields that must always be present in the raw review result dict.
_REQUIRED_TOP_LEVEL_FIELDS = ("verdict", "confidence", "findings", "scope_reviewed")

# Top-level fields for planner results.
_REQUIRED_PLANNER_FIELDS = ("epic", "tickets")

# Top-level fields for planning review results.
_REQUIRED_PLANNING_REVIEW_FIELDS = (
    "verdict",
    "confidence",
    "findings",
    "scope_reviewed",
)


@dataclass
class ValidationResult:
    """Result of validating a ReviewResult, PlannerResult, or PlanningReviewResult."""

    is_valid: bool
    violations: list[str] = field(default_factory=list)
    result: ReviewResult | PlannerResult | PlanningReviewResult | None = None


def validate_review_result(raw_data: dict, criteria_ids: list[str]) -> ValidationResult:
    """Validate a raw review result dict per adapters.md semantic rules.

    This function is the single source of truth for review-result validation.
    It takes a raw dict (before ReviewResult.from_dict() materialization) so
    that top-level field presence is always enforced — from_dict() silently
    defaults missing fields, so checking after materialization is too late.

    On success, the materialized ReviewResult is returned in
    ValidationResult.result for downstream use.

    Args:
        raw_data: The raw dict to validate and materialize.
        criteria_ids: Valid criterion IDs from the run request's acceptance_criteria.

    Returns:
        ValidationResult with is_valid=True and a materialized result if all
        rules pass, else is_valid=False with violation descriptions.
    """
    violations: list[str] = []
    criteria_id_set = set(criteria_ids)

    # All top-level fields must be present
    for field_name in _REQUIRED_TOP_LEVEL_FIELDS:
        if field_name not in raw_data:
            violations.append(f"required top-level field '{field_name}' is missing")

    # If required fields are missing, we cannot safely materialize or run
    # semantic checks — return early.
    if violations:
        return ValidationResult(is_valid=False, violations=violations)

    # Materialize the ReviewResult. from_dict / __post_init__ may raise on
    # invalid enum values — treat that as a validation failure.
    try:
        result = ReviewResult.from_dict(raw_data)
    except (ValueError, KeyError, TypeError) as exc:
        violations.append(f"failed to parse review result: {exc}")
        return ValidationResult(is_valid=False, violations=violations)

    # verdict:fail requires >=1 blocking finding
    if result.verdict == "fail":
        blocking = [f for f in result.findings if f.severity == "blocking"]
        if not blocking:
            violations.append(
                "verdict is 'fail' but no findings have severity 'blocking'"
            )

    # verdict:pass cannot have blocking findings
    if result.verdict == "pass":
        blocking = [f for f in result.findings if f.severity == "blocking"]
        if blocking:
            violations.append(
                "verdict is 'pass' but findings include severity 'blocking'"
            )

    # confidence:high invalid with empty files_examined
    if result.confidence == "high":
        if not result.scope_reviewed.files_examined:
            violations.append("confidence is 'high' but files_examined is empty")

    # confidence:high invalid with criteria provided but criteria_checked empty
    if result.confidence == "high":
        if criteria_ids and not result.scope_reviewed.criteria_checked:
            violations.append(
                "confidence is 'high' with criteria provided but criteria_checked is empty"
            )

    # criteria_checked entries must reference valid criterion IDs
    for cc in result.scope_reviewed.criteria_checked:
        if cc.criterion_id not in criteria_id_set:
            violations.append(
                f"criteria_checked references unknown criterion_id '{cc.criterion_id}'"
            )

    # acceptance_criterion_id on findings must reference valid criterion IDs
    for f in result.findings:
        if f.acceptance_criterion_id is not None:
            if f.acceptance_criterion_id not in criteria_id_set:
                violations.append(
                    f"finding references unknown acceptance_criterion_id "
                    f"'{f.acceptance_criterion_id}'"
                )

    return ValidationResult(
        is_valid=len(violations) == 0,
        violations=violations,
        result=result if not violations else None,
    )


# ---------------------------------------------------------------------------
# Planner result validation (T02)
# ---------------------------------------------------------------------------


def _has_cycle(adj: dict[int, list[int]]) -> bool:
    """Return True if the adjacency list contains a cycle (DFS)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {node: WHITE for node in adj}

    def dfs(node: int) -> bool:
        color[node] = GRAY
        for neighbour in adj.get(node, []):
            if color.get(neighbour) == GRAY:
                return True
            if color.get(neighbour) == WHITE and dfs(neighbour):
                return True
        color[node] = BLACK
        return False

    return any(dfs(n) for n in adj if color[n] == WHITE)


def validate_planner_result(raw_data: dict) -> ValidationResult:
    """Validate a raw planner result dict per T02 semantic rules.

    Semantic rules:
    - sequences must be unique and contiguous starting from 1
    - dependency references must point to valid sequence numbers
    - dependency references must not create cycles
    - each ticket must have at least one acceptance criterion

    Args:
        raw_data: The raw dict to validate and materialize.

    Returns:
        ValidationResult with is_valid=True and a materialized PlannerResult
        if all rules pass, else is_valid=False with violation descriptions.
    """
    violations: list[str] = []

    # Required top-level fields
    for field_name in _REQUIRED_PLANNER_FIELDS:
        if field_name not in raw_data:
            violations.append(f"required top-level field '{field_name}' is missing")

    if violations:
        return ValidationResult(is_valid=False, violations=violations)

    # Materialize
    try:
        result = PlannerResult.from_dict(raw_data)
    except (ValueError, KeyError, TypeError) as exc:
        violations.append(f"failed to parse planner result: {exc}")
        return ValidationResult(is_valid=False, violations=violations)

    # Must have at least one ticket
    if not result.tickets:
        violations.append("tickets array must contain at least one ticket")

    # Sequence uniqueness and contiguity
    sequences = [t.sequence for t in result.tickets]
    seq_set = set(sequences)
    if len(seq_set) != len(sequences):
        violations.append("ticket sequences are not unique")
    expected = set(range(1, len(sequences) + 1))
    if seq_set != expected:
        violations.append(
            f"ticket sequences must be contiguous starting from 1; "
            f"got {sorted(sequences)}, expected {sorted(expected)}"
        )

    # Dependency references and cycle detection
    adj: dict[int, list[int]] = {t.sequence: [] for t in result.tickets}
    for t in result.tickets:
        for dep in t.dependencies:
            if dep not in seq_set:
                violations.append(
                    f"ticket #{t.sequence} depends on sequence #{dep} "
                    f"which does not exist in the plan"
                )
            elif dep == t.sequence:
                violations.append(
                    f"ticket #{t.sequence} depends on itself"
                )
            else:
                adj[t.sequence].append(dep)

    if seq_set == expected and _has_cycle(adj):
        violations.append("ticket dependencies contain a cycle")

    # Each ticket must have at least one acceptance criterion
    for t in result.tickets:
        if not t.acceptance_criteria:
            violations.append(
                f"ticket #{t.sequence} has no acceptance criteria"
            )

    return ValidationResult(
        is_valid=len(violations) == 0,
        violations=violations,
        result=result if not violations else None,
    )


# ---------------------------------------------------------------------------
# Planning review result validation (T02)
# ---------------------------------------------------------------------------


def validate_planning_review_result(
    raw_data: dict,
    valid_sequences: list[int],
) -> ValidationResult:
    """Validate a raw planning review result dict per T02 semantic rules.

    Semantic rules (mirrors implementation review plus planning-specific):
    - fail requires at least one blocking finding
    - pass forbids blocking findings
    - high confidence requires non-empty tickets_reviewed
    - target_type "ticket" requires target_sequence to be a valid sequence
    - target_type "epic" requires target_sequence to be null
    - tickets_reviewed entries must reference valid sequence numbers

    Args:
        raw_data: The raw dict to validate and materialize.
        valid_sequences: Valid ticket sequence numbers from the plan.

    Returns:
        ValidationResult with is_valid=True and a materialized
        PlanningReviewResult if all rules pass, else is_valid=False.
    """
    violations: list[str] = []
    seq_set = set(valid_sequences)

    # Required top-level fields
    for field_name in _REQUIRED_PLANNING_REVIEW_FIELDS:
        if field_name not in raw_data:
            violations.append(f"required top-level field '{field_name}' is missing")

    if violations:
        return ValidationResult(is_valid=False, violations=violations)

    # Materialize
    try:
        result = PlanningReviewResult.from_dict(raw_data)
    except (ValueError, KeyError, TypeError) as exc:
        violations.append(f"failed to parse planning review result: {exc}")
        return ValidationResult(is_valid=False, violations=violations)

    # verdict:fail requires >=1 blocking finding
    if result.verdict == "fail":
        blocking = [f for f in result.findings if f.severity == "blocking"]
        if not blocking:
            violations.append(
                "verdict is 'fail' but no findings have severity 'blocking'"
            )

    # verdict:pass cannot have blocking findings
    if result.verdict == "pass":
        blocking = [f for f in result.findings if f.severity == "blocking"]
        if blocking:
            violations.append(
                "verdict is 'pass' but findings include severity 'blocking'"
            )

    # confidence:high requires non-empty tickets_reviewed
    if result.confidence == "high":
        if not result.scope_reviewed.tickets_reviewed:
            violations.append(
                "confidence is 'high' but tickets_reviewed is empty"
            )

    # target_type / target_sequence consistency
    for f in result.findings:
        if f.target_type == "ticket":
            if f.target_sequence is None:
                violations.append(
                    f"finding targets a ticket but target_sequence is null: "
                    f"{f.description!r}"
                )
            elif f.target_sequence not in seq_set:
                violations.append(
                    f"finding references unknown ticket sequence "
                    f"#{f.target_sequence}: {f.description!r}"
                )
        elif f.target_type == "epic":
            if f.target_sequence is not None:
                violations.append(
                    f"finding targets epic but target_sequence is not null "
                    f"(got {f.target_sequence}): {f.description!r}"
                )

    # tickets_reviewed entries must reference valid sequences
    for seq in result.scope_reviewed.tickets_reviewed:
        if seq not in seq_set:
            violations.append(
                f"tickets_reviewed references unknown sequence #{seq}"
            )

    return ValidationResult(
        is_valid=len(violations) == 0,
        violations=violations,
        result=result if not violations else None,
    )
