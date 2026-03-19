"""Tests for review result validation (T17)."""

from __future__ import annotations

from capsaicin.validation import validate_review_result


def _raw(**overrides) -> dict:
    """Build a valid raw review result dict with sensible defaults."""
    defaults = {
        "verdict": "pass",
        "confidence": "high",
        "findings": [],
        "scope_reviewed": {
            "files_examined": ["src/foo.py"],
            "tests_run": True,
            "criteria_checked": [{"criterion_id": "ac-1", "description": "Works"}],
        },
    }
    defaults.update(overrides)
    return defaults


def _blocking_finding(**overrides) -> dict:
    defaults = {
        "severity": "blocking",
        "category": "correctness",
        "description": "Bug found",
        "disposition": "open",
    }
    defaults.update(overrides)
    return defaults


def _warning_finding(**overrides) -> dict:
    defaults = {
        "severity": "warning",
        "category": "style",
        "description": "Nit",
        "disposition": "open",
    }
    defaults.update(overrides)
    return defaults


CRITERIA_IDS = ["ac-1", "ac-2"]


class TestValidResult:
    def test_valid_pass(self):
        vr = validate_review_result(_raw(), CRITERIA_IDS)
        assert vr.is_valid is True
        assert vr.violations == []
        assert vr.result is not None
        assert vr.result.verdict == "pass"

    def test_valid_fail(self):
        vr = validate_review_result(
            _raw(verdict="fail", findings=[_blocking_finding()]),
            CRITERIA_IDS,
        )
        assert vr.is_valid is True
        assert vr.result is not None

    def test_valid_escalate(self):
        vr = validate_review_result(_raw(verdict="escalate"), CRITERIA_IDS)
        assert vr.is_valid is True

    def test_pass_with_warnings_valid(self):
        vr = validate_review_result(
            _raw(findings=[_warning_finding()]),
            CRITERIA_IDS,
        )
        assert vr.is_valid is True

    def test_valid_with_no_criteria_provided(self):
        """confidence:high is fine if no criteria were provided."""
        vr = validate_review_result(
            _raw(
                scope_reviewed={
                    "files_examined": ["a.py"],
                    "tests_run": True,
                    "criteria_checked": [],
                }
            ),
            [],
        )
        assert vr.is_valid is True


class TestVerdictFail:
    def test_fail_without_blocking_finding(self):
        vr = validate_review_result(
            _raw(verdict="fail", findings=[_warning_finding()]),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        assert any("'fail'" in v and "blocking" in v for v in vr.violations)

    def test_fail_with_no_findings(self):
        vr = validate_review_result(
            _raw(verdict="fail", findings=[]),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False


class TestVerdictPass:
    def test_pass_with_blocking_finding(self):
        vr = validate_review_result(
            _raw(verdict="pass", findings=[_blocking_finding()]),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        assert any("'pass'" in v and "blocking" in v for v in vr.violations)


class TestConfidenceHigh:
    def test_high_confidence_empty_files_examined(self):
        vr = validate_review_result(
            _raw(
                scope_reviewed={
                    "files_examined": [],
                    "tests_run": True,
                    "criteria_checked": [{"criterion_id": "ac-1", "description": "X"}],
                }
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        assert any("files_examined" in v for v in vr.violations)

    def test_high_confidence_criteria_provided_but_none_checked(self):
        vr = validate_review_result(
            _raw(
                scope_reviewed={
                    "files_examined": ["a.py"],
                    "tests_run": True,
                    "criteria_checked": [],
                }
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        assert any("criteria_checked" in v for v in vr.violations)

    def test_medium_confidence_skips_files_check(self):
        vr = validate_review_result(
            _raw(
                confidence="medium",
                scope_reviewed={
                    "files_examined": [],
                    "tests_run": False,
                    "criteria_checked": [],
                },
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is True

    def test_low_confidence_skips_files_check(self):
        vr = validate_review_result(
            _raw(
                confidence="low",
                scope_reviewed={
                    "files_examined": [],
                    "tests_run": False,
                    "criteria_checked": [],
                },
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is True


class TestCriterionIdValidation:
    def test_criteria_checked_unknown_id(self):
        vr = validate_review_result(
            _raw(
                scope_reviewed={
                    "files_examined": ["a.py"],
                    "tests_run": True,
                    "criteria_checked": [
                        {"criterion_id": "ac-1", "description": "OK"},
                        {"criterion_id": "bogus", "description": "Bad"},
                    ],
                }
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        assert any("bogus" in v for v in vr.violations)

    def test_finding_unknown_acceptance_criterion_id(self):
        vr = validate_review_result(
            _raw(
                verdict="fail",
                findings=[_blocking_finding(acceptance_criterion_id="nonexistent")],
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        assert any("nonexistent" in v for v in vr.violations)

    def test_finding_null_criterion_id_is_valid(self):
        vr = validate_review_result(
            _raw(
                verdict="fail",
                findings=[_blocking_finding(acceptance_criterion_id=None)],
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is True

    def test_finding_valid_criterion_id(self):
        vr = validate_review_result(
            _raw(
                verdict="fail",
                findings=[_blocking_finding(acceptance_criterion_id="ac-1")],
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is True


class TestTopLevelFieldPresence:
    """Top-level fields must always be present per adapters.md."""

    def test_all_fields_present_valid(self):
        vr = validate_review_result(_raw(), CRITERIA_IDS)
        assert vr.is_valid is True

    def test_missing_verdict(self):
        raw = _raw()
        del raw["verdict"]
        vr = validate_review_result(raw, CRITERIA_IDS)
        assert vr.is_valid is False
        assert any("'verdict'" in v for v in vr.violations)

    def test_missing_confidence(self):
        raw = _raw()
        del raw["confidence"]
        vr = validate_review_result(raw, CRITERIA_IDS)
        assert vr.is_valid is False
        assert any("'confidence'" in v for v in vr.violations)

    def test_missing_findings(self):
        raw = _raw()
        del raw["findings"]
        vr = validate_review_result(raw, CRITERIA_IDS)
        assert vr.is_valid is False
        assert any("'findings'" in v for v in vr.violations)

    def test_missing_scope_reviewed(self):
        raw = _raw()
        del raw["scope_reviewed"]
        vr = validate_review_result(raw, CRITERIA_IDS)
        assert vr.is_valid is False
        assert any("'scope_reviewed'" in v for v in vr.violations)

    def test_multiple_fields_missing(self):
        vr = validate_review_result({"verdict": "pass"}, CRITERIA_IDS)
        assert vr.is_valid is False
        assert len([v for v in vr.violations if "missing" in v]) == 3

    def test_missing_fields_returns_no_result(self):
        raw = _raw()
        del raw["findings"]
        vr = validate_review_result(raw, CRITERIA_IDS)
        assert vr.result is None

    def test_invalid_enum_in_raw_data(self):
        """Invalid enum values caught during materialization."""
        vr = validate_review_result(
            _raw(verdict="maybe"),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        assert any("parse" in v.lower() for v in vr.violations)


class TestMultipleViolations:
    def test_accumulates_all_violations(self):
        """Multiple rules violated simultaneously should all be reported."""
        vr = validate_review_result(
            _raw(
                verdict="pass",
                confidence="high",
                findings=[_blocking_finding(acceptance_criterion_id="bogus")],
                scope_reviewed={
                    "files_examined": [],
                    "tests_run": False,
                    "criteria_checked": [],
                },
            ),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        # Should have at least: pass+blocking, empty files, empty criteria_checked, bogus id
        assert len(vr.violations) >= 4

    def test_valid_result_returned_on_success(self):
        vr = validate_review_result(_raw(), CRITERIA_IDS)
        assert vr.is_valid is True
        assert vr.result is not None
        assert vr.result.verdict == "pass"
        assert vr.result.confidence == "high"

    def test_no_result_on_failure(self):
        vr = validate_review_result(
            _raw(verdict="fail", findings=[]),
            CRITERIA_IDS,
        )
        assert vr.is_valid is False
        assert vr.result is None
