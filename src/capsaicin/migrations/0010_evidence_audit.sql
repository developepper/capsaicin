-- T09: Evidence audit trail and inspectability.
-- Adds a join table linking agent runs to the evidence records included in
-- their prompts, and an append-only event table for requirement status changes.

-- 1. Join table: which evidence records were included in each agent run's prompt.
CREATE TABLE IF NOT EXISTS agent_run_evidence (
    run_id      TEXT NOT NULL REFERENCES agent_runs(id),
    evidence_id TEXT NOT NULL REFERENCES backend_evidence(id),
    PRIMARY KEY (run_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_run_evidence_evidence
    ON agent_run_evidence(evidence_id);

-- 2. Append-only audit events for evidence requirement status changes.
CREATE TABLE IF NOT EXISTS evidence_requirement_events (
    id              TEXT PRIMARY KEY,
    requirement_id  TEXT NOT NULL REFERENCES evidence_requirements(id),
    event_type      TEXT NOT NULL CHECK (event_type IN (
                        'created', 'satisfied', 'waived', 'reset_to_pending'
                    )),
    evidence_id     TEXT REFERENCES backend_evidence(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_evidence_requirement_events_req
    ON evidence_requirement_events(requirement_id, created_at);
