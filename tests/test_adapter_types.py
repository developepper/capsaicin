"""Tests for adapter contract types (T10)."""

from __future__ import annotations

import json

import pytest

from capsaicin.adapters.base import BaseAdapter
from capsaicin.adapters.types import (
    AcceptanceCriterion,
    CriterionChecked,
    Finding,
    ReviewResult,
    RunRequest,
    RunResult,
    ScopeReviewed,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _sample_finding(**overrides) -> Finding:
    defaults = {
        "severity": "blocking",
        "category": "correctness",
        "description": "Missing null check",
        "location": "src/foo.py:42",
        "acceptance_criterion_id": "crit-01",
        "disposition": "open",
    }
    defaults.update(overrides)
    return Finding(**defaults)


def _sample_review_result(**overrides) -> ReviewResult:
    defaults = {
        "verdict": "fail",
        "confidence": "high",
        "findings": [_sample_finding()],
        "scope_reviewed": ScopeReviewed(
            files_examined=["src/foo.py"],
            tests_run=True,
            criteria_checked=[
                CriterionChecked(criterion_id="crit-01", description="Works")
            ],
        ),
    }
    defaults.update(overrides)
    return ReviewResult(**defaults)


def _sample_run_request(**overrides) -> RunRequest:
    defaults = {
        "run_id": "run-001",
        "role": "implementer",
        "mode": "read-write",
        "working_directory": "/tmp/repo",
        "prompt": "Implement the feature",
        "diff_context": None,
        "context_files": ["src/main.py"],
        "acceptance_criteria": [
            AcceptanceCriterion(id="ac-1", description="It works", status="pending")
        ],
        "prior_findings": [],
        "timeout_seconds": 300,
        "adapter_config": {"model": "opus"},
    }
    defaults.update(overrides)
    return RunRequest(**defaults)


def _sample_run_result(**overrides) -> RunResult:
    defaults = {
        "run_id": "run-001",
        "exit_status": "success",
        "duration_seconds": 12.5,
        "raw_stdout": "done",
        "raw_stderr": "",
        "structured_result": None,
        "adapter_metadata": {"session_id": "sess-1"},
    }
    defaults.update(overrides)
    return RunResult(**defaults)


# ---------------------------------------------------------------------------
# AcceptanceCriterion
# ---------------------------------------------------------------------------


class TestAcceptanceCriterion:
    def test_round_trip(self):
        ac = AcceptanceCriterion(id="ac-1", description="Works", status="met")
        assert AcceptanceCriterion.from_dict(ac.to_dict()) == ac

    def test_default_status(self):
        ac = AcceptanceCriterion(id="ac-1", description="Works")
        assert ac.status == "pending"

    def test_from_dict_missing_status_defaults(self):
        ac = AcceptanceCriterion.from_dict({"id": "ac-1", "description": "Works"})
        assert ac.status == "pending"


# ---------------------------------------------------------------------------
# CriterionChecked
# ---------------------------------------------------------------------------


class TestCriterionChecked:
    def test_round_trip(self):
        cc = CriterionChecked(criterion_id="c-1", description="Check it")
        assert CriterionChecked.from_dict(cc.to_dict()) == cc


# ---------------------------------------------------------------------------
# ScopeReviewed
# ---------------------------------------------------------------------------


class TestScopeReviewed:
    def test_round_trip(self):
        sr = ScopeReviewed(
            files_examined=["a.py", "b.py"],
            tests_run=True,
            criteria_checked=[CriterionChecked(criterion_id="c-1", description="OK")],
        )
        assert ScopeReviewed.from_dict(sr.to_dict()) == sr

    def test_defaults(self):
        sr = ScopeReviewed()
        assert sr.files_examined == []
        assert sr.tests_run is False
        assert sr.criteria_checked == []

    def test_from_dict_empty(self):
        sr = ScopeReviewed.from_dict({})
        assert sr.files_examined == []
        assert sr.tests_run is False
        assert sr.criteria_checked == []


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


class TestFinding:
    def test_round_trip(self):
        f = _sample_finding()
        assert Finding.from_dict(f.to_dict()) == f

    def test_optional_fields_default_none(self):
        f = Finding(severity="warning", category="style", description="Nit")
        assert f.location is None
        assert f.acceptance_criterion_id is None
        assert f.disposition == "open"

    def test_from_dict_optional_absent(self):
        f = Finding.from_dict(
            {"severity": "info", "category": "docs", "description": "Add docstring"}
        )
        assert f.location is None
        assert f.acceptance_criterion_id is None
        assert f.disposition == "open"


# ---------------------------------------------------------------------------
# ReviewResult
# ---------------------------------------------------------------------------


class TestReviewResult:
    def test_round_trip_dict(self):
        rr = _sample_review_result()
        assert ReviewResult.from_dict(rr.to_dict()) == rr

    def test_round_trip_json(self):
        rr = _sample_review_result()
        assert ReviewResult.from_json(rr.to_json()) == rr

    def test_pass_verdict(self):
        rr = _sample_review_result(verdict="pass", findings=[])
        d = rr.to_dict()
        assert d["verdict"] == "pass"
        assert d["findings"] == []

    def test_escalate_verdict(self):
        rr = _sample_review_result(verdict="escalate", findings=[])
        assert rr.verdict == "escalate"

    def test_empty_defaults(self):
        rr = ReviewResult(verdict="pass", confidence="high")
        assert rr.findings == []
        assert rr.scope_reviewed.files_examined == []

    def test_from_dict_preserves_nested(self):
        rr = _sample_review_result()
        d = rr.to_dict()
        restored = ReviewResult.from_dict(d)
        assert len(restored.findings) == 1
        assert restored.findings[0].severity == "blocking"
        assert restored.scope_reviewed.tests_run is True
        assert len(restored.scope_reviewed.criteria_checked) == 1


# ---------------------------------------------------------------------------
# RunRequest
# ---------------------------------------------------------------------------


class TestRunRequest:
    def test_round_trip_dict(self):
        req = _sample_run_request()
        assert RunRequest.from_dict(req.to_dict()) == req

    def test_round_trip_json(self):
        req = _sample_run_request()
        assert RunRequest.from_json(req.to_json()) == req

    def test_implementer_request(self):
        req = _sample_run_request(role="implementer", mode="read-write")
        d = req.to_dict()
        assert d["role"] == "implementer"
        assert d["mode"] == "read-write"
        assert d["diff_context"] is None

    def test_reviewer_request(self):
        req = _sample_run_request(
            role="reviewer",
            mode="read-only",
            diff_context="--- a/foo.py\n+++ b/foo.py",
            prior_findings=[_sample_finding()],
        )
        d = req.to_dict()
        assert d["role"] == "reviewer"
        assert d["mode"] == "read-only"
        assert d["diff_context"] is not None
        assert len(d["prior_findings"]) == 1

    def test_defaults(self):
        req = RunRequest(
            run_id="r",
            role="implementer",
            mode="read-write",
            working_directory="/tmp",
            prompt="go",
        )
        assert req.diff_context is None
        assert req.context_files == []
        assert req.acceptance_criteria == []
        assert req.prior_findings == []
        assert req.timeout_seconds == 300
        assert req.adapter_config == {}

    def test_from_dict_defaults(self):
        req = RunRequest.from_dict(
            {
                "run_id": "r",
                "role": "implementer",
                "mode": "read-write",
                "working_directory": "/tmp",
                "prompt": "go",
            }
        )
        assert req.timeout_seconds == 300
        assert req.adapter_config == {}
        assert req.context_files == []

    def test_acceptance_criteria_nested(self):
        req = _sample_run_request()
        d = req.to_dict()
        restored = RunRequest.from_dict(d)
        assert len(restored.acceptance_criteria) == 1
        assert restored.acceptance_criteria[0].id == "ac-1"
        assert restored.acceptance_criteria[0].status == "pending"


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------


class TestRunResult:
    def test_round_trip_dict(self):
        res = _sample_run_result()
        assert RunResult.from_dict(res.to_dict()) == res

    def test_round_trip_json(self):
        res = _sample_run_result()
        assert RunResult.from_json(res.to_json()) == res

    def test_without_structured_result(self):
        res = _sample_run_result(structured_result=None)
        d = res.to_dict()
        assert d["structured_result"] is None
        restored = RunResult.from_dict(d)
        assert restored.structured_result is None

    def test_with_structured_result(self):
        rr = _sample_review_result()
        res = _sample_run_result(structured_result=rr)
        d = res.to_dict()
        assert d["structured_result"] is not None
        assert d["structured_result"]["verdict"] == "fail"
        restored = RunResult.from_dict(d)
        assert restored.structured_result is not None
        assert restored.structured_result.verdict == "fail"
        assert len(restored.structured_result.findings) == 1

    def test_defaults(self):
        res = RunResult(run_id="r", exit_status="success")
        assert res.duration_seconds == 0.0
        assert res.raw_stdout == ""
        assert res.raw_stderr == ""
        assert res.structured_result is None
        assert res.adapter_metadata == {}

    def test_all_exit_statuses(self):
        for status in (
            "success",
            "failure",
            "timeout",
            "contract_violation",
            "parse_error",
        ):
            res = RunResult(run_id="r", exit_status=status)
            assert res.exit_status == status
            assert RunResult.from_dict(res.to_dict()).exit_status == status

    def test_full_round_trip_json(self):
        """Full integration: RunResult with ReviewResult, through JSON."""
        rr = _sample_review_result()
        res = _sample_run_result(structured_result=rr)
        json_str = res.to_json()
        parsed = json.loads(json_str)
        assert parsed["structured_result"]["verdict"] == "fail"
        restored = RunResult.from_json(json_str)
        assert restored == res


# ---------------------------------------------------------------------------
# BaseAdapter
# ---------------------------------------------------------------------------


class TestBaseAdapter:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseAdapter()

    def test_subclass_works(self):
        class StubAdapter(BaseAdapter):
            def execute(self, request: RunRequest) -> RunResult:
                return RunResult(run_id=request.run_id, exit_status="success")

        adapter = StubAdapter()
        req = _sample_run_request()
        result = adapter.execute(req)
        assert result.run_id == "run-001"
        assert result.exit_status == "success"


# ---------------------------------------------------------------------------
# Enum validation (negative tests)
# ---------------------------------------------------------------------------


class TestEnumValidation:
    """Invalid enum values must be rejected at construction time."""

    def test_finding_invalid_severity(self):
        with pytest.raises(ValueError, match="severity"):
            Finding(severity="critical", category="x", description="x")

    def test_finding_invalid_disposition(self):
        with pytest.raises(ValueError, match="disposition"):
            Finding(
                severity="blocking",
                category="x",
                description="x",
                disposition="ignored",
            )

    def test_review_result_invalid_verdict(self):
        with pytest.raises(ValueError, match="verdict"):
            ReviewResult(verdict="maybe", confidence="high")

    def test_review_result_invalid_confidence(self):
        with pytest.raises(ValueError, match="confidence"):
            ReviewResult(verdict="pass", confidence="certain")

    def test_run_request_invalid_role(self):
        with pytest.raises(ValueError, match="role"):
            RunRequest(
                run_id="r",
                role="worker",
                mode="read-write",
                working_directory="/tmp",
                prompt="go",
            )

    def test_run_request_invalid_mode(self):
        with pytest.raises(ValueError, match="mode"):
            RunRequest(
                run_id="r",
                role="implementer",
                mode="rw",
                working_directory="/tmp",
                prompt="go",
            )

    def test_run_result_invalid_exit_status(self):
        with pytest.raises(ValueError, match="exit_status"):
            RunResult(run_id="r", exit_status="crashed")

    def test_acceptance_criterion_invalid_status(self):
        with pytest.raises(ValueError, match="status"):
            AcceptanceCriterion(id="ac-1", description="x", status="done")

    def test_from_dict_also_validates(self):
        """Validation fires through from_dict path too."""
        with pytest.raises(ValueError, match="verdict"):
            ReviewResult.from_dict({"verdict": "nope", "confidence": "high"})

    def test_from_json_also_validates(self):
        with pytest.raises(ValueError, match="exit_status"):
            RunResult.from_json(
                json.dumps(
                    {
                        "run_id": "r",
                        "exit_status": "exploded",
                    }
                )
            )
