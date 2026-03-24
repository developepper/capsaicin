-- Add 'waived' as an allowed status for evidence_requirements.
-- SQLite does not support ALTER CHECK, so we recreate the table.

CREATE TABLE IF NOT EXISTS evidence_requirements_new (
    id                TEXT PRIMARY KEY,
    epic_id           TEXT NOT NULL REFERENCES planned_epics(id),
    planned_ticket_id TEXT REFERENCES planned_tickets(id),
    description       TEXT NOT NULL,
    suggested_command TEXT,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','fulfilled','waived')),
    fulfilled_by      TEXT REFERENCES backend_evidence(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO evidence_requirements_new
    SELECT id, epic_id, planned_ticket_id, description, suggested_command,
           status, fulfilled_by, created_at, updated_at
    FROM evidence_requirements;

DROP TABLE evidence_requirements;

ALTER TABLE evidence_requirements_new RENAME TO evidence_requirements;

CREATE INDEX IF NOT EXISTS idx_evidence_requirements_epic
    ON evidence_requirements(epic_id);

CREATE INDEX IF NOT EXISTS idx_evidence_requirements_ticket
    ON evidence_requirements(planned_ticket_id);

CREATE INDEX IF NOT EXISTS idx_evidence_requirements_status
    ON evidence_requirements(epic_id, status);
