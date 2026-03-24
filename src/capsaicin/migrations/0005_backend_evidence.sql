-- Backend validation evidence data model.
-- Stores structured evidence (command output, structured results,
-- permission-denial observations, behavioral notes) attached to
-- epics and planned tickets.

-- ===================================================================
-- 1. backend_evidence – individual evidence items
-- ===================================================================

CREATE TABLE IF NOT EXISTS backend_evidence (
    id                TEXT PRIMARY KEY,
    epic_id           TEXT NOT NULL REFERENCES planned_epics(id),
    planned_ticket_id TEXT REFERENCES planned_tickets(id),
    evidence_type     TEXT NOT NULL CHECK (evidence_type IN (
                          'command_output','structured_result',
                          'permission_denial','behavioral_note'
                      )),
    title             TEXT NOT NULL,
    body              TEXT,
    command           TEXT,
    stdout            TEXT,
    stderr            TEXT,
    structured_data   TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_backend_evidence_epic
    ON backend_evidence(epic_id);

CREATE INDEX IF NOT EXISTS idx_backend_evidence_ticket
    ON backend_evidence(planned_ticket_id);

CREATE INDEX IF NOT EXISTS idx_backend_evidence_type
    ON backend_evidence(epic_id, evidence_type);

-- ===================================================================
-- 2. evidence_requirements – what evidence is needed
-- ===================================================================

CREATE TABLE IF NOT EXISTS evidence_requirements (
    id                TEXT PRIMARY KEY,
    epic_id           TEXT NOT NULL REFERENCES planned_epics(id),
    planned_ticket_id TEXT REFERENCES planned_tickets(id),
    description       TEXT NOT NULL,
    suggested_command TEXT,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','fulfilled')),
    fulfilled_by      TEXT REFERENCES backend_evidence(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_evidence_requirements_epic
    ON evidence_requirements(epic_id);

CREATE INDEX IF NOT EXISTS idx_evidence_requirements_ticket
    ON evidence_requirements(planned_ticket_id);

CREATE INDEX IF NOT EXISTS idx_evidence_requirements_status
    ON evidence_requirements(epic_id, status);
