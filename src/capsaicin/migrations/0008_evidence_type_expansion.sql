-- Expand evidence_type CHECK constraint to include all five ticket-spec types
-- (command, output_envelope, structured_result_sample) alongside the original
-- four types (command_output, structured_result, permission_denial, behavioral_note).

CREATE TABLE IF NOT EXISTS backend_evidence_new (
    id                TEXT PRIMARY KEY,
    epic_id           TEXT NOT NULL REFERENCES planned_epics(id),
    planned_ticket_id TEXT REFERENCES planned_tickets(id),
    evidence_type     TEXT NOT NULL CHECK (evidence_type IN (
                          'command','output_envelope','structured_result_sample',
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

INSERT INTO backend_evidence_new
    SELECT id, epic_id, planned_ticket_id, evidence_type, title,
           body, command, stdout, stderr, structured_data, created_at, updated_at
    FROM backend_evidence;

DROP TABLE backend_evidence;

ALTER TABLE backend_evidence_new RENAME TO backend_evidence;

CREATE INDEX IF NOT EXISTS idx_backend_evidence_epic
    ON backend_evidence(epic_id);

CREATE INDEX IF NOT EXISTS idx_backend_evidence_ticket
    ON backend_evidence(planned_ticket_id);

CREATE INDEX IF NOT EXISTS idx_backend_evidence_type
    ON backend_evidence(epic_id, evidence_type);
