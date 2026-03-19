"""Finding reconciliation across review cycles (T19).

Uses lightweight fingerprinting to match, update, close, and create
findings across implement-review cycles.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from ulid import ULID

from capsaicin.adapters.types import Finding


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_fingerprint(category: str, location: str | None, description: str) -> str:
    """Compute a reconciliation fingerprint.

    The fingerprint is ``(category, location, description_prefix)`` where
    *description_prefix* is the first 80 characters of the description,
    normalised to lowercase with collapsed whitespace.
    """
    prefix = re.sub(r"\s+", " ", description[:80].lower()).strip()
    loc = location or ""
    return f"{category}|{loc}|{prefix}"


def reconcile_findings(
    conn: sqlite3.Connection,
    ticket_id: str,
    review_run_id: str,
    impl_run_id: str,
    new_findings: list[Finding],
    verdict: str,
    is_first_cycle: bool,
) -> None:
    """Reconcile incoming findings against prior open findings.

    Behaviour depends on *is_first_cycle* and *verdict*:

    - **First cycle**: persist all *new_findings* as new rows with
      generated ULIDs.
    - **verdict pass** (re-review): bulk-close all prior open findings
      for the ticket with ``resolved_in_run = impl_run_id``.
    - **verdict fail** (re-review): match incoming findings to prior
      open findings by fingerprint.  Update matched findings' description
      and severity (preserving the original ID).  Close unmatched prior
      findings.  Create unmatched new findings.
    """
    now = _now()

    if is_first_cycle:
        _persist_new_findings(conn, ticket_id, review_run_id, new_findings, now)
        return

    if verdict == "pass":
        _bulk_close_open(conn, ticket_id, impl_run_id, now)
        # A passing review may still include warning/info findings
        # (adapters.md:154). Persist them so the review record is complete.
        if new_findings:
            _persist_new_findings(conn, ticket_id, review_run_id, new_findings, now)
        return

    # verdict == "fail" (or escalate with findings)
    _reconcile_fail(conn, ticket_id, review_run_id, impl_run_id, new_findings, now)


def _persist_new_findings(
    conn: sqlite3.Connection,
    ticket_id: str,
    review_run_id: str,
    findings: list[Finding],
    now: str,
) -> None:
    for f in findings:
        fid = str(ULID())
        fp = compute_fingerprint(f.category, f.location, f.description)
        conn.execute(
            "INSERT INTO findings "
            "(id, run_id, ticket_id, acceptance_criterion_id, severity, "
            "category, location, fingerprint, description, disposition, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                fid,
                review_run_id,
                ticket_id,
                f.acceptance_criterion_id,
                f.severity,
                f.category,
                f.location,
                fp,
                f.description,
                now,
                now,
            ),
        )
    conn.commit()


def _bulk_close_open(
    conn: sqlite3.Connection,
    ticket_id: str,
    impl_run_id: str,
    now: str,
) -> None:
    conn.execute(
        "UPDATE findings SET disposition = 'fixed', resolved_in_run = ?, "
        "updated_at = ? WHERE ticket_id = ? AND disposition = 'open'",
        (impl_run_id, now, ticket_id),
    )
    conn.commit()


def _reconcile_fail(
    conn: sqlite3.Connection,
    ticket_id: str,
    review_run_id: str,
    impl_run_id: str,
    new_findings: list[Finding],
    now: str,
) -> None:
    # Load prior open findings
    prior_rows = conn.execute(
        "SELECT id, fingerprint FROM findings "
        "WHERE ticket_id = ? AND disposition = 'open'",
        (ticket_id,),
    ).fetchall()
    prior_by_fp: dict[str, str] = {r["fingerprint"]: r["id"] for r in prior_rows}
    matched_prior_ids: set[str] = set()

    for f in new_findings:
        fp = compute_fingerprint(f.category, f.location, f.description)

        if fp in prior_by_fp:
            # Matched: update description and severity, link to new review run
            prior_id = prior_by_fp[fp]
            matched_prior_ids.add(prior_id)
            conn.execute(
                "UPDATE findings SET description = ?, severity = ?, "
                "run_id = ?, fingerprint = ?, updated_at = ? WHERE id = ?",
                (f.description, f.severity, review_run_id, fp, now, prior_id),
            )
        else:
            # Unmatched new: create
            fid = str(ULID())
            conn.execute(
                "INSERT INTO findings "
                "(id, run_id, ticket_id, acceptance_criterion_id, severity, "
                "category, location, fingerprint, description, disposition, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (
                    fid,
                    review_run_id,
                    ticket_id,
                    f.acceptance_criterion_id,
                    f.severity,
                    f.category,
                    f.location,
                    fp,
                    f.description,
                    now,
                    now,
                ),
            )

    # Close unmatched prior findings
    unmatched_prior_ids = {r["id"] for r in prior_rows} - matched_prior_ids
    for pid in unmatched_prior_ids:
        conn.execute(
            "UPDATE findings SET disposition = 'fixed', resolved_in_run = ?, "
            "updated_at = ? WHERE id = ?",
            (impl_run_id, now, pid),
        )

    conn.commit()
