"""Review result validation (T17).

Pure validation of a raw review result dict against adapters.md rules.
No DB access, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from capsaicin.adapters.types import ReviewResult

# Top-level fields that must always be present in the raw review result dict.
_REQUIRED_TOP_LEVEL_FIELDS = ("verdict", "confidence", "findings", "scope_reviewed")


@dataclass
class ValidationResult:
    """Result of validating a ReviewResult."""

    is_valid: bool
    violations: list[str] = field(default_factory=list)
    result: ReviewResult | None = None


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
