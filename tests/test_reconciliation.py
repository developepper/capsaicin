"""Tests for finding reconciliation (T19)."""

from __future__ import annotations

import pytest

from capsaicin.adapters.types import Finding
from capsaicin.db import get_connection, run_migrations
from capsaicin.reconciliation import compute_fingerprint, reconcile_findings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    conn = get_connection(":memory:")
    run_migrations(conn)
    conn.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES ('p1', 'test', '/tmp')"
    )
    conn.execute(
        "INSERT INTO tickets (id, project_id, title, description) "
        "VALUES ('t1', 'p1', 'Test', 'desc')"
    )
    # Two runs: impl and review
    for rid in ("impl-1", "review-1", "impl-2", "review-2"):
        conn.execute(
            "INSERT INTO agent_runs "
            "(id, ticket_id, role, mode, cycle_number, exit_status, "
            "prompt, run_request, started_at) "
            "VALUES (?, 't1', 'implementer', 'read-write', 1, 'success', "
            "'p', '{}', datetime('now'))",
            (rid,),
        )
    conn.commit()
    return conn


def _open_findings(db, ticket_id="t1"):
    return db.execute(
        "SELECT * FROM findings WHERE ticket_id = ? AND disposition = 'open' "
        "ORDER BY created_at",
        (ticket_id,),
    ).fetchall()


def _all_findings(db, ticket_id="t1"):
    return db.execute(
        "SELECT * FROM findings WHERE ticket_id = ? ORDER BY created_at",
        (ticket_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# compute_fingerprint
# ---------------------------------------------------------------------------


class TestComputeFingerprint:
    def test_deterministic(self):
        fp1 = compute_fingerprint("cat", "file.py:10", "Some issue here")
        fp2 = compute_fingerprint("cat", "file.py:10", "Some issue here")
        assert fp1 == fp2

    def test_differs_on_description_prefix(self):
        fp1 = compute_fingerprint("cat", "file.py:10", "Issue A")
        fp2 = compute_fingerprint("cat", "file.py:10", "Issue B")
        assert fp1 != fp2

    def test_same_category_location_different_desc(self):
        fp1 = compute_fingerprint("correctness", None, "missing null check")
        fp2 = compute_fingerprint("correctness", None, "missing error handling")
        assert fp1 != fp2

    def test_lowercased(self):
        fp1 = compute_fingerprint("Cat", "Loc", "UPPER CASE DESC")
        fp2 = compute_fingerprint("Cat", "Loc", "upper case desc")
        assert fp1 == fp2

    def test_whitespace_collapsed(self):
        fp1 = compute_fingerprint("cat", "loc", "too   many    spaces")
        fp2 = compute_fingerprint("cat", "loc", "too many spaces")
        assert fp1 == fp2

    def test_truncated_at_80_chars(self):
        long_a = "x" * 100
        long_b = "x" * 80 + "different suffix"
        fp1 = compute_fingerprint("cat", None, long_a)
        fp2 = compute_fingerprint("cat", None, long_b)
        assert fp1 == fp2

    def test_null_location(self):
        fp = compute_fingerprint("cat", None, "desc")
        assert "|" in fp  # location slot is empty but present


# ---------------------------------------------------------------------------
# First cycle: all findings persisted as new
# ---------------------------------------------------------------------------


class TestFirstCycle:
    def test_all_persisted(self, db):
        findings = [
            Finding(severity="blocking", category="correctness", description="bug"),
            Finding(severity="warning", category="style", description="naming"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            findings,
            verdict="fail",
            is_first_cycle=True,
        )
        rows = _open_findings(db)
        assert len(rows) == 2

    def test_ulid_ids_generated(self, db):
        findings = [
            Finding(severity="blocking", category="cat", description="desc"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            findings,
            verdict="fail",
            is_first_cycle=True,
        )
        rows = _open_findings(db)
        assert len(rows[0]["id"]) == 26  # ULID length

    def test_fingerprints_stored(self, db):
        findings = [
            Finding(severity="blocking", category="cat", description="desc"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            findings,
            verdict="fail",
            is_first_cycle=True,
        )
        row = _open_findings(db)[0]
        expected_fp = compute_fingerprint("cat", None, "desc")
        assert row["fingerprint"] == expected_fp

    def test_empty_findings_noop(self, db):
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            [],
            verdict="pass",
            is_first_cycle=True,
        )
        assert len(_all_findings(db)) == 0


# ---------------------------------------------------------------------------
# Pass verdict: bulk-close all prior open findings
# ---------------------------------------------------------------------------


class TestPassVerdict:
    def test_bulk_close(self, db):
        # Create prior open findings
        prior = [
            Finding(severity="blocking", category="a", description="issue 1"),
            Finding(severity="warning", category="b", description="issue 2"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            prior,
            verdict="fail",
            is_first_cycle=True,
        )
        assert len(_open_findings(db)) == 2

        # Pass verdict closes all
        reconcile_findings(
            db,
            "t1",
            "review-2",
            "impl-2",
            [],
            verdict="pass",
            is_first_cycle=False,
        )
        assert len(_open_findings(db)) == 0

        all_f = _all_findings(db)
        assert all(dict(f)["disposition"] == "fixed" for f in all_f)
        assert all(dict(f)["resolved_in_run"] == "impl-2" for f in all_f)

    def test_pass_with_warning_findings_persisted(self, db):
        # Prior blocking finding from first cycle
        prior = [
            Finding(severity="blocking", category="a", description="old bug"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            prior,
            verdict="fail",
            is_first_cycle=True,
        )
        assert len(_open_findings(db)) == 1

        # Pass verdict with a new warning finding (adapters.md:154 allows this)
        new_warnings = [
            Finding(severity="warning", category="style", description="naming nit"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-2",
            "impl-2",
            new_warnings,
            verdict="pass",
            is_first_cycle=False,
        )

        # Prior blocking finding should be closed
        all_f = _all_findings(db)
        closed = [dict(f) for f in all_f if f["disposition"] == "fixed"]
        assert len(closed) == 1
        assert closed[0]["description"] == "old bug"

        # New warning should be persisted as open
        open_f = _open_findings(db)
        assert len(open_f) == 1
        assert dict(open_f[0])["severity"] == "warning"
        assert dict(open_f[0])["description"] == "naming nit"


# ---------------------------------------------------------------------------
# Fail verdict: match, close unmatched prior, create unmatched new
# ---------------------------------------------------------------------------


class TestFailVerdict:
    def test_matched_updated(self, db):
        # First cycle: create a finding with a long description so the
        # first 80 chars stay the same when we append detail later.
        base_desc = "x" * 80
        prior = [
            Finding(severity="blocking", category="correctness", description=base_desc),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            prior,
            verdict="fail",
            is_first_cycle=True,
        )
        original_id = _open_findings(db)[0]["id"]

        # Second cycle: same 80-char prefix → same fingerprint, but
        # updated description suffix and severity
        updated = [
            Finding(
                severity="warning",
                category="correctness",
                description=base_desc + " (partially fixed)",
            ),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-2",
            "impl-2",
            updated,
            verdict="fail",
            is_first_cycle=False,
        )

        rows = _open_findings(db)
        assert len(rows) == 1
        row = dict(rows[0])
        # Original ID preserved
        assert row["id"] == original_id
        # Description and severity updated
        assert row["description"] == base_desc + " (partially fixed)"
        assert row["severity"] == "warning"
        # Linked to new review run
        assert row["run_id"] == "review-2"

    def test_unmatched_prior_closed(self, db):
        prior = [
            Finding(severity="blocking", category="a", description="old issue"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            prior,
            verdict="fail",
            is_first_cycle=True,
        )

        # Second cycle: completely different finding
        new = [
            Finding(severity="blocking", category="b", description="new issue"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-2",
            "impl-2",
            new,
            verdict="fail",
            is_first_cycle=False,
        )

        open_f = _open_findings(db)
        assert len(open_f) == 1
        assert dict(open_f[0])["description"] == "new issue"

        all_f = _all_findings(db)
        assert len(all_f) == 2
        closed = [dict(f) for f in all_f if f["disposition"] == "fixed"]
        assert len(closed) == 1
        assert closed[0]["description"] == "old issue"
        assert closed[0]["resolved_in_run"] == "impl-2"

    def test_unmatched_new_created(self, db):
        prior = [
            Finding(severity="blocking", category="a", description="existing"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            prior,
            verdict="fail",
            is_first_cycle=True,
        )

        # Second cycle: keep existing + add new
        new = [
            Finding(severity="blocking", category="a", description="existing"),
            Finding(severity="blocking", category="c", description="brand new"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-2",
            "impl-2",
            new,
            verdict="fail",
            is_first_cycle=False,
        )

        open_f = _open_findings(db)
        assert len(open_f) == 2
        descs = {dict(f)["description"] for f in open_f}
        assert descs == {"existing", "brand new"}

    def test_mixed_match_close_create(self, db):
        prior = [
            Finding(severity="blocking", category="a", description="stays"),
            Finding(severity="blocking", category="b", description="goes away"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-1",
            "impl-1",
            prior,
            verdict="fail",
            is_first_cycle=True,
        )
        stays_id = None
        for f in _open_findings(db):
            if f["description"] == "stays":
                stays_id = f["id"]

        new = [
            Finding(severity="warning", category="a", description="stays"),
            Finding(severity="blocking", category="c", description="new one"),
        ]
        reconcile_findings(
            db,
            "t1",
            "review-2",
            "impl-2",
            new,
            verdict="fail",
            is_first_cycle=False,
        )

        open_f = _open_findings(db)
        assert len(open_f) == 2
        open_dict = {dict(f)["description"]: dict(f) for f in open_f}

        # "stays" preserved its original ID, updated severity
        assert open_dict["stays"]["id"] == stays_id
        assert open_dict["stays"]["severity"] == "warning"

        # "new one" is a new finding
        assert open_dict["new one"]["severity"] == "blocking"

        # "goes away" is closed
        all_f = _all_findings(db)
        closed = [dict(f) for f in all_f if f["disposition"] == "fixed"]
        assert len(closed) == 1
        assert closed[0]["description"] == "goes away"
