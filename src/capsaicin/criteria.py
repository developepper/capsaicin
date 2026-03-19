"""Acceptance criteria updates from review results (T18).

Updates criterion statuses based on reviewer output per the rule in
cli.md:189-200.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from capsaicin.adapters.types import ReviewResult


def update_criteria_from_review(
    conn: sqlite3.Connection,
    ticket_id: str,
    review_result: ReviewResult,
) -> None:
    """Update acceptance-criteria statuses based on a review result.

    Rules:
    - Match ``criteria_checked`` entries to ``acceptance_criteria`` rows
      by ``criterion_id``.
    - If a checked criterion has a blocking finding with a matching
      ``acceptance_criterion_id``, mark it ``unmet``.
    - If a checked criterion has no blocking finding with a matching
      ``acceptance_criterion_id``, mark it ``met``.
    - If a criterion was not checked in this review, leave its status
      unchanged.
    - Findings with ``acceptance_criterion_id = None`` are general
      findings not tied to a specific criterion.
    """
    # Build set of checked criterion IDs
    checked_ids = {
        c.criterion_id for c in review_result.scope_reviewed.criteria_checked
    }
    if not checked_ids:
        return

    # Build set of criterion IDs that have a blocking finding
    blocked_criterion_ids: set[str] = set()
    for finding in review_result.findings:
        if (
            finding.severity == "blocking"
            and finding.acceptance_criterion_id is not None
        ):
            blocked_criterion_ids.add(finding.acceptance_criterion_id)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for criterion_id in checked_ids:
        if criterion_id in blocked_criterion_ids:
            new_status = "unmet"
        else:
            new_status = "met"

        conn.execute(
            "UPDATE acceptance_criteria SET status = ?, updated_at = ? "
            "WHERE id = ? AND ticket_id = ?",
            (new_status, now, criterion_id, ticket_id),
        )

    conn.commit()
