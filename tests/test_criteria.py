"""Tests for acceptance criteria updates (T18)."""

from __future__ import annotations

import pytest

from capsaicin.adapters.types import (
    CriterionChecked,
    Finding,
    ReviewResult,
    ScopeReviewed,
)
from capsaicin.criteria import update_criteria_from_review
from capsaicin.db import get_connection, run_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    conn = get_connection(":memory:")
    run_migrations(conn)
    # Insert project and ticket
    conn.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES ('p1', 'test', '/tmp')"
    )
    conn.execute(
        "INSERT INTO tickets (id, project_id, title, description) "
        "VALUES ('t1', 'p1', 'Test', 'desc')"
    )
    conn.commit()
    return conn


def _add_criterion(db, cid, desc="criterion", status="pending"):
    db.execute(
        "INSERT INTO acceptance_criteria (id, ticket_id, description, status) "
        "VALUES (?, 't1', ?, ?)",
        (cid, desc, status),
    )
    db.commit()


def _get_status(db, cid):
    return db.execute(
        "SELECT status FROM acceptance_criteria WHERE id = ?", (cid,)
    ).fetchone()["status"]


def _review(
    verdict="pass",
    confidence="high",
    findings=None,
    criteria_checked=None,
    files_examined=None,
):
    return ReviewResult(
        verdict=verdict,
        confidence=confidence,
        findings=findings or [],
        scope_reviewed=ScopeReviewed(
            files_examined=files_examined or ["file.py"],
            criteria_checked=criteria_checked or [],
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCheckedWithNoBlockingFinding:
    def test_marked_met(self, db):
        _add_criterion(db, "c1")
        result = _review(
            criteria_checked=[CriterionChecked(criterion_id="c1", description="c")],
        )
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "met"

    def test_warning_finding_still_met(self, db):
        _add_criterion(db, "c1")
        result = _review(
            criteria_checked=[CriterionChecked(criterion_id="c1", description="c")],
            findings=[
                Finding(
                    severity="warning",
                    category="style",
                    description="minor issue",
                    acceptance_criterion_id="c1",
                ),
            ],
        )
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "met"

    def test_info_finding_still_met(self, db):
        _add_criterion(db, "c1")
        result = _review(
            criteria_checked=[CriterionChecked(criterion_id="c1", description="c")],
            findings=[
                Finding(
                    severity="info",
                    category="note",
                    description="fyi",
                    acceptance_criterion_id="c1",
                ),
            ],
        )
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "met"


class TestCheckedWithBlockingFinding:
    def test_marked_unmet(self, db):
        _add_criterion(db, "c1")
        result = _review(
            verdict="fail",
            criteria_checked=[CriterionChecked(criterion_id="c1", description="c")],
            findings=[
                Finding(
                    severity="blocking",
                    category="correctness",
                    description="broken",
                    acceptance_criterion_id="c1",
                ),
            ],
        )
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "unmet"

    def test_previously_met_becomes_unmet(self, db):
        _add_criterion(db, "c1", status="met")
        result = _review(
            verdict="fail",
            criteria_checked=[CriterionChecked(criterion_id="c1", description="c")],
            findings=[
                Finding(
                    severity="blocking",
                    category="regression",
                    description="broke it",
                    acceptance_criterion_id="c1",
                ),
            ],
        )
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "unmet"


class TestUncheckedCriterion:
    def test_unchanged(self, db):
        _add_criterion(db, "c1", status="pending")
        _add_criterion(db, "c2", status="met")
        # Only check c1, leave c2 unchecked
        result = _review(
            criteria_checked=[CriterionChecked(criterion_id="c1", description="c")],
        )
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "met"
        assert _get_status(db, "c2") == "met"  # unchanged

    def test_no_criteria_checked_noop(self, db):
        _add_criterion(db, "c1", status="pending")
        result = _review(criteria_checked=[])
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "pending"


class TestNullCriterionId:
    def test_general_finding_does_not_affect_criteria(self, db):
        _add_criterion(db, "c1")
        result = _review(
            verdict="fail",
            criteria_checked=[CriterionChecked(criterion_id="c1", description="c")],
            findings=[
                Finding(
                    severity="blocking",
                    category="general",
                    description="general issue",
                    acceptance_criterion_id=None,
                ),
            ],
        )
        update_criteria_from_review(db, "t1", result)
        # c1 was checked but the blocking finding has no criterion link → met
        assert _get_status(db, "c1") == "met"


class TestMultipleCriteria:
    def test_mixed_results(self, db):
        _add_criterion(db, "c1")
        _add_criterion(db, "c2")
        _add_criterion(db, "c3", status="pending")
        result = _review(
            verdict="fail",
            criteria_checked=[
                CriterionChecked(criterion_id="c1", description="c1"),
                CriterionChecked(criterion_id="c2", description="c2"),
                # c3 not checked
            ],
            findings=[
                Finding(
                    severity="blocking",
                    category="correctness",
                    description="c2 is broken",
                    acceptance_criterion_id="c2",
                ),
            ],
        )
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "met"
        assert _get_status(db, "c2") == "unmet"
        assert _get_status(db, "c3") == "pending"  # unchanged

    def test_multiple_blocking_findings_same_criterion(self, db):
        _add_criterion(db, "c1")
        result = _review(
            verdict="fail",
            criteria_checked=[CriterionChecked(criterion_id="c1", description="c")],
            findings=[
                Finding(
                    severity="blocking",
                    category="a",
                    description="issue 1",
                    acceptance_criterion_id="c1",
                ),
                Finding(
                    severity="blocking",
                    category="b",
                    description="issue 2",
                    acceptance_criterion_id="c1",
                ),
            ],
        )
        update_criteria_from_review(db, "t1", result)
        assert _get_status(db, "c1") == "unmet"
