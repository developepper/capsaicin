"""Tests for planner and planning-review result validation (T02)."""

from __future__ import annotations

from capsaicin.validation import validate_planner_result, validate_planning_review_result


# ---------------------------------------------------------------------------
# Planner result helpers
# ---------------------------------------------------------------------------


def _ticket(**overrides) -> dict:
    """Build a valid planned ticket dict."""
    defaults = {
        "sequence": 1,
        "title": "Do something",
        "goal": "Achieve something",
        "scope": ["item A"],
        "non_goals": [],
        "acceptance_criteria": [{"description": "It works"}],
        "dependencies": [],
        "references": [],
        "implementation_notes": [],
    }
    defaults.update(overrides)
    return defaults


def _planner_raw(**overrides) -> dict:
    """Build a valid raw planner result dict."""
    defaults = {
        "epic": {
            "title": "Epic Title",
            "summary": "Epic summary",
            "success_outcome": "Everything works",
        },
        "tickets": [_ticket(sequence=1), _ticket(sequence=2, title="Second")],
        "sequencing_notes": None,
        "open_questions": [],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Planning review result helpers
# ---------------------------------------------------------------------------

VALID_SEQUENCES = [1, 2, 3]


def _planning_finding(**overrides) -> dict:
    defaults = {
        "severity": "warning",
        "category": "scope",
        "target_type": "ticket",
        "target_sequence": 1,
        "description": "Scope too broad",
        "disposition": "open",
    }
    defaults.update(overrides)
    return defaults


def _blocking_planning_finding(**overrides) -> dict:
    defaults = _planning_finding(severity="blocking")
    defaults.update(overrides)
    return defaults


def _review_raw(**overrides) -> dict:
    """Build a valid raw planning review result dict."""
    defaults = {
        "verdict": "pass",
        "confidence": "high",
        "findings": [],
        "scope_reviewed": {
            "epic_reviewed": True,
            "tickets_reviewed": [1, 2, 3],
            "aspects_checked": ["scope", "dependencies"],
        },
    }
    defaults.update(overrides)
    return defaults


# ===========================================================================
# Planner result validation tests
# ===========================================================================


class TestPlannerValid:
    def test_valid_plan(self):
        vr = validate_planner_result(_planner_raw())
        assert vr.is_valid is True
        assert vr.violations == []
        assert vr.result is not None

    def test_valid_with_dependencies(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1),
                    _ticket(sequence=2, dependencies=[1]),
                ]
            )
        )
        assert vr.is_valid is True

    def test_valid_with_open_questions(self):
        vr = validate_planner_result(
            _planner_raw(open_questions=["How to handle edge case?"])
        )
        assert vr.is_valid is True

    def test_valid_with_sequencing_notes(self):
        vr = validate_planner_result(
            _planner_raw(sequencing_notes="Do ticket 1 before 2")
        )
        assert vr.is_valid is True


class TestPlannerMissingFields:
    def test_missing_epic(self):
        raw = _planner_raw()
        del raw["epic"]
        vr = validate_planner_result(raw)
        assert vr.is_valid is False
        assert any("'epic'" in v for v in vr.violations)

    def test_missing_tickets(self):
        raw = _planner_raw()
        del raw["tickets"]
        vr = validate_planner_result(raw)
        assert vr.is_valid is False
        assert any("'tickets'" in v for v in vr.violations)

    def test_missing_fields_returns_no_result(self):
        raw = _planner_raw()
        del raw["epic"]
        vr = validate_planner_result(raw)
        assert vr.result is None


class TestPlannerSequences:
    def test_non_contiguous_sequences(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[_ticket(sequence=1), _ticket(sequence=3, title="Third")]
            )
        )
        assert vr.is_valid is False
        assert any("contiguous" in v for v in vr.violations)

    def test_duplicate_sequences(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[_ticket(sequence=1), _ticket(sequence=1, title="Dup")]
            )
        )
        assert vr.is_valid is False
        assert any("unique" in v for v in vr.violations)

    def test_sequences_not_starting_from_1(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[_ticket(sequence=2), _ticket(sequence=3, title="Third")]
            )
        )
        assert vr.is_valid is False
        assert any("contiguous" in v for v in vr.violations)

    def test_single_ticket_sequence_1(self):
        vr = validate_planner_result(
            _planner_raw(tickets=[_ticket(sequence=1)])
        )
        assert vr.is_valid is True


class TestPlannerDependencies:
    def test_dependency_on_nonexistent_sequence(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1),
                    _ticket(sequence=2, dependencies=[99]),
                ]
            )
        )
        assert vr.is_valid is False
        assert any("#99" in v for v in vr.violations)

    def test_self_dependency(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1, dependencies=[1]),
                    _ticket(sequence=2, title="Second"),
                ]
            )
        )
        assert vr.is_valid is False
        assert any("itself" in v for v in vr.violations)

    def test_dependency_cycle(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1, dependencies=[2]),
                    _ticket(sequence=2, dependencies=[1], title="Second"),
                ]
            )
        )
        assert vr.is_valid is False
        assert any("cycle" in v for v in vr.violations)

    def test_transitive_cycle(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1, dependencies=[3]),
                    _ticket(sequence=2, dependencies=[1], title="Second"),
                    _ticket(sequence=3, dependencies=[2], title="Third"),
                ]
            )
        )
        assert vr.is_valid is False
        assert any("cycle" in v for v in vr.violations)

    def test_valid_chain_no_cycle(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1),
                    _ticket(sequence=2, dependencies=[1], title="Second"),
                    _ticket(sequence=3, dependencies=[2], title="Third"),
                ]
            )
        )
        assert vr.is_valid is True

    def test_diamond_dependency_no_cycle(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1),
                    _ticket(sequence=2, dependencies=[1], title="Second"),
                    _ticket(sequence=3, dependencies=[1], title="Third"),
                    _ticket(
                        sequence=4,
                        dependencies=[2, 3],
                        title="Fourth",
                    ),
                ]
            )
        )
        assert vr.is_valid is True


class TestPlannerAcceptanceCriteria:
    def test_ticket_with_no_criteria(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1, acceptance_criteria=[]),
                    _ticket(sequence=2, title="Second"),
                ]
            )
        )
        assert vr.is_valid is False
        assert any("#1" in v and "acceptance criteria" in v for v in vr.violations)

    def test_all_tickets_have_criteria(self):
        vr = validate_planner_result(_planner_raw())
        assert vr.is_valid is True


class TestPlannerParseErrors:
    def test_invalid_epic_structure(self):
        vr = validate_planner_result(
            _planner_raw(epic={"title": "T"})  # missing required fields
        )
        assert vr.is_valid is False
        assert any("parse" in v.lower() for v in vr.violations)

    def test_empty_tickets_array(self):
        vr = validate_planner_result(_planner_raw(tickets=[]))
        assert vr.is_valid is False
        assert any("at least one" in v for v in vr.violations)


class TestPlannerMultipleViolations:
    def test_accumulates_violations(self):
        vr = validate_planner_result(
            _planner_raw(
                tickets=[
                    _ticket(sequence=1, acceptance_criteria=[], dependencies=[5]),
                    _ticket(sequence=3, acceptance_criteria=[], title="Third"),
                ]
            )
        )
        assert vr.is_valid is False
        # contiguous, invalid dep, two missing criteria
        assert len(vr.violations) >= 3


# ===========================================================================
# Planning review result validation tests
# ===========================================================================


class TestPlanningReviewValid:
    def test_valid_pass(self):
        vr = validate_planning_review_result(_review_raw(), VALID_SEQUENCES)
        assert vr.is_valid is True
        assert vr.violations == []
        assert vr.result is not None
        assert vr.result.verdict == "pass"

    def test_valid_fail(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="fail",
                findings=[_blocking_planning_finding()],
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is True

    def test_valid_escalate(self):
        vr = validate_planning_review_result(
            _review_raw(verdict="escalate"), VALID_SEQUENCES
        )
        assert vr.is_valid is True

    def test_valid_epic_level_finding(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="fail",
                findings=[
                    _blocking_planning_finding(
                        target_type="epic", target_sequence=None
                    )
                ],
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is True


class TestPlanningReviewMissingFields:
    def test_missing_verdict(self):
        raw = _review_raw()
        del raw["verdict"]
        vr = validate_planning_review_result(raw, VALID_SEQUENCES)
        assert vr.is_valid is False
        assert any("'verdict'" in v for v in vr.violations)

    def test_missing_scope_reviewed(self):
        raw = _review_raw()
        del raw["scope_reviewed"]
        vr = validate_planning_review_result(raw, VALID_SEQUENCES)
        assert vr.is_valid is False

    def test_missing_fields_returns_no_result(self):
        raw = _review_raw()
        del raw["findings"]
        vr = validate_planning_review_result(raw, VALID_SEQUENCES)
        assert vr.result is None


class TestPlanningReviewVerdictFail:
    def test_fail_without_blocking(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="fail",
                findings=[_planning_finding(severity="warning")],
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        assert any("'fail'" in v and "blocking" in v for v in vr.violations)

    def test_fail_with_no_findings(self):
        vr = validate_planning_review_result(
            _review_raw(verdict="fail", findings=[]),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False


class TestPlanningReviewVerdictPass:
    def test_pass_with_blocking(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="pass",
                findings=[_blocking_planning_finding()],
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        assert any("'pass'" in v and "blocking" in v for v in vr.violations)

    def test_pass_with_warnings_valid(self):
        vr = validate_planning_review_result(
            _review_raw(findings=[_planning_finding(severity="warning")]),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is True


class TestPlanningReviewConfidence:
    def test_high_confidence_empty_tickets_reviewed(self):
        vr = validate_planning_review_result(
            _review_raw(
                scope_reviewed={
                    "epic_reviewed": True,
                    "tickets_reviewed": [],
                    "aspects_checked": [],
                }
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        assert any("tickets_reviewed" in v for v in vr.violations)

    def test_medium_confidence_empty_tickets_reviewed_ok(self):
        vr = validate_planning_review_result(
            _review_raw(
                confidence="medium",
                scope_reviewed={
                    "epic_reviewed": True,
                    "tickets_reviewed": [],
                    "aspects_checked": [],
                },
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is True


class TestPlanningReviewTargetType:
    def test_ticket_finding_null_sequence(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="fail",
                findings=[
                    _blocking_planning_finding(
                        target_type="ticket", target_sequence=None
                    )
                ],
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        assert any("target_sequence is null" in v for v in vr.violations)

    def test_ticket_finding_invalid_sequence(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="fail",
                findings=[
                    _blocking_planning_finding(
                        target_type="ticket", target_sequence=99
                    )
                ],
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        assert any("#99" in v for v in vr.violations)

    def test_epic_finding_with_sequence(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="fail",
                findings=[
                    _blocking_planning_finding(
                        target_type="epic", target_sequence=1
                    )
                ],
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        assert any("not null" in v for v in vr.violations)

    def test_epic_finding_null_sequence_valid(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="fail",
                findings=[
                    _blocking_planning_finding(
                        target_type="epic", target_sequence=None
                    )
                ],
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is True


class TestPlanningReviewTicketsReviewed:
    def test_unknown_sequence_in_tickets_reviewed(self):
        vr = validate_planning_review_result(
            _review_raw(
                scope_reviewed={
                    "epic_reviewed": True,
                    "tickets_reviewed": [1, 99],
                    "aspects_checked": [],
                }
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        assert any("#99" in v for v in vr.violations)


class TestPlanningReviewParseErrors:
    def test_invalid_enum(self):
        vr = validate_planning_review_result(
            _review_raw(verdict="maybe"),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        assert any("parse" in v.lower() for v in vr.violations)


class TestPlanningReviewMultipleViolations:
    def test_accumulates_violations(self):
        vr = validate_planning_review_result(
            _review_raw(
                verdict="pass",
                confidence="high",
                findings=[
                    _blocking_planning_finding(
                        target_type="ticket", target_sequence=None
                    )
                ],
                scope_reviewed={
                    "epic_reviewed": False,
                    "tickets_reviewed": [],
                    "aspects_checked": [],
                },
            ),
            VALID_SEQUENCES,
        )
        assert vr.is_valid is False
        # pass+blocking, empty tickets_reviewed, null target_sequence
        assert len(vr.violations) >= 3
