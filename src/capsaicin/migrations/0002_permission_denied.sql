-- T01: Add 'permission_denied' as a distinct run outcome and gate reason.
-- SQLite CHECK constraints cannot be altered in place, so we recreate
-- the affected tables.

-- 1. Recreate agent_runs with the extended exit_status CHECK.
CREATE TABLE agent_runs_new (
    id                TEXT PRIMARY KEY,
    ticket_id         TEXT NOT NULL REFERENCES tickets(id),
    role              TEXT NOT NULL CHECK (role IN ('implementer','reviewer','planner','human')),
    mode              TEXT NOT NULL CHECK (mode IN ('read-write','read-only')),
    cycle_number      INTEGER NOT NULL,
    attempt_number    INTEGER NOT NULL DEFAULT 1,
    exit_status       TEXT NOT NULL CHECK (exit_status IN (
                          'running','success','failure','timeout',
                          'contract_violation','parse_error','permission_denied'
                      )),
    verdict           TEXT CHECK (verdict IN ('pass','fail','escalate')),
    prompt            TEXT NOT NULL,
    run_request       TEXT NOT NULL,
    diff_context      TEXT,
    raw_stdout        TEXT,
    raw_stderr        TEXT,
    structured_result TEXT,
    duration_seconds  REAL,
    adapter_metadata  TEXT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT
);

INSERT INTO agent_runs_new SELECT * FROM agent_runs;
DROP TABLE agent_runs;
ALTER TABLE agent_runs_new RENAME TO agent_runs;

-- Recreate indexes on agent_runs
CREATE INDEX IF NOT EXISTS idx_agent_runs_ticket_role
    ON agent_runs(ticket_id, role, started_at);

-- 2. Recreate tickets with the extended gate_reason CHECK.
CREATE TABLE tickets_new (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    current_cycle INTEGER NOT NULL DEFAULT 0,
    current_impl_attempt INTEGER NOT NULL DEFAULT 1,
    current_review_attempt INTEGER NOT NULL DEFAULT 1,
    blocked_reason TEXT,
    gate_reason TEXT CHECK (gate_reason IN (
                    'review_passed','reviewer_escalated','cycle_limit',
                    'implementation_failure','human_requested',
                    'empty_implementation','low_confidence_pass',
                    'permission_denied'
                )),
    status      TEXT NOT NULL DEFAULT 'ready'
                CHECK (status IN (
                    'ready','implementing','in-review',
                    'revise','human-gate','pr-ready',
                    'blocked','done'
                )),
    status_changed_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO tickets_new SELECT * FROM tickets;
DROP TABLE tickets;
ALTER TABLE tickets_new RENAME TO tickets;

-- Recreate indexes on tickets
CREATE INDEX IF NOT EXISTS idx_tickets_project_status
    ON tickets(project_id, status);
