-- T09 fix: Make audit tables durable by removing FK constraints on evidence_id.
-- When evidence is deleted, audit rows should be preserved (not cascade-deleted).
-- SQLite does not support ALTER CONSTRAINT, so we recreate the tables.

-- 1. Recreate agent_run_evidence without FK on evidence_id.
CREATE TABLE agent_run_evidence_new (
    run_id      TEXT NOT NULL REFERENCES agent_runs(id),
    evidence_id TEXT NOT NULL,
    PRIMARY KEY (run_id, evidence_id)
);

INSERT INTO agent_run_evidence_new SELECT * FROM agent_run_evidence;
DROP TABLE agent_run_evidence;
ALTER TABLE agent_run_evidence_new RENAME TO agent_run_evidence;

CREATE INDEX IF NOT EXISTS idx_agent_run_evidence_evidence
    ON agent_run_evidence(evidence_id);

-- 2. Recreate evidence_requirement_events without FK on evidence_id.
CREATE TABLE evidence_requirement_events_new (
    id              TEXT PRIMARY KEY,
    requirement_id  TEXT NOT NULL REFERENCES evidence_requirements(id),
    event_type      TEXT NOT NULL CHECK (event_type IN (
                        'created', 'satisfied', 'waived', 'reset_to_pending'
                    )),
    evidence_id     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO evidence_requirement_events_new SELECT * FROM evidence_requirement_events;
DROP TABLE evidence_requirement_events;
ALTER TABLE evidence_requirement_events_new RENAME TO evidence_requirement_events;

CREATE INDEX IF NOT EXISTS idx_evidence_requirement_events_req
    ON evidence_requirement_events(requirement_id, created_at);
