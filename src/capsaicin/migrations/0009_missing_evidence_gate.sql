-- T08: Add 'missing_evidence' as a gate_reason for planned_epics.
-- SQLite CHECK constraints cannot be altered in place, so we recreate
-- the planned_epics table with the extended constraint.

-- 1. Recreate planned_epics with the extended gate_reason CHECK.
CREATE TABLE planned_epics_new (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id),
    problem_statement TEXT NOT NULL,
    title             TEXT,
    summary           TEXT,
    success_outcome   TEXT,
    sequencing_notes  TEXT,
    current_cycle     INTEGER NOT NULL DEFAULT 0,
    current_draft_attempt  INTEGER NOT NULL DEFAULT 1,
    current_review_attempt INTEGER NOT NULL DEFAULT 1,
    blocked_reason    TEXT,
    gate_reason       TEXT CHECK (gate_reason IN (
                          'review_passed','reviewer_escalated','cycle_limit',
                          'draft_failure','human_requested','low_confidence_pass',
                          'missing_evidence'
                      )),
    status            TEXT NOT NULL DEFAULT 'new'
                      CHECK (status IN (
                          'new','drafting','in-review',
                          'revise','human-gate','approved','blocked'
                      )),
    materialized_path TEXT,
    status_changed_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO planned_epics_new SELECT * FROM planned_epics;
DROP TABLE planned_epics;
ALTER TABLE planned_epics_new RENAME TO planned_epics;

-- Recreate indexes on planned_epics
CREATE INDEX IF NOT EXISTS idx_planned_epics_project_status
    ON planned_epics(project_id, status);
