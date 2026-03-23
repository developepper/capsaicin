-- Epic 04 / T01: Planning data model foundation.
-- Adds planning tables, generalises shared tables with epic_id + XOR checks,
-- extends orchestrator_state for planning loops, and adds lineage FK on tickets.

-- ===================================================================
-- 1. New planning tables (in FK dependency order)
-- ===================================================================

CREATE TABLE IF NOT EXISTS planned_epics (
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
                          'draft_failure','human_requested','low_confidence_pass'
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

CREATE TABLE IF NOT EXISTS planned_tickets (
    id                TEXT PRIMARY KEY,
    epic_id           TEXT NOT NULL REFERENCES planned_epics(id),
    sequence          INTEGER NOT NULL,
    title             TEXT NOT NULL,
    goal              TEXT NOT NULL,
    scope             TEXT NOT NULL,
    non_goals         TEXT NOT NULL,
    references_       TEXT NOT NULL,
    implementation_notes TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (epic_id, sequence)
);

CREATE TABLE IF NOT EXISTS planned_ticket_criteria (
    id                TEXT PRIMARY KEY,
    planned_ticket_id TEXT NOT NULL REFERENCES planned_tickets(id),
    description       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS planned_ticket_dependencies (
    planned_ticket_id TEXT NOT NULL REFERENCES planned_tickets(id),
    depends_on_id     TEXT NOT NULL REFERENCES planned_tickets(id),
    PRIMARY KEY (planned_ticket_id, depends_on_id),
    CHECK (planned_ticket_id != depends_on_id)
);

CREATE TABLE IF NOT EXISTS planning_findings (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES agent_runs(id),
    epic_id         TEXT NOT NULL REFERENCES planned_epics(id),
    planned_ticket_id TEXT REFERENCES planned_tickets(id),
    severity        TEXT NOT NULL CHECK (severity IN ('blocking','warning','info')),
    category        TEXT NOT NULL,
    description     TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    disposition     TEXT NOT NULL DEFAULT 'open'
                    CHECK (disposition IN ('open','fixed','wont_fix','disputed')),
    resolved_in_run TEXT REFERENCES agent_runs(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS materialization_hashes (
    epic_id           TEXT NOT NULL REFERENCES planned_epics(id),
    planned_ticket_id TEXT REFERENCES planned_tickets(id),
    file_path         TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    materialized_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (epic_id, file_path)
);

-- ===================================================================
-- 2. Recreate agent_runs with nullable ticket_id, new epic_id, XOR check
-- ===================================================================

CREATE TABLE agent_runs_new (
    id                TEXT PRIMARY KEY,
    ticket_id         TEXT REFERENCES tickets(id),
    epic_id           TEXT REFERENCES planned_epics(id),
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
    finished_at       TEXT,
    CHECK (
        (ticket_id IS NOT NULL AND epic_id IS NULL) OR
        (ticket_id IS NULL AND epic_id IS NOT NULL)
    )
);

INSERT INTO agent_runs_new (
    id, ticket_id, epic_id, role, mode, cycle_number, attempt_number,
    exit_status, verdict, prompt, run_request, diff_context,
    raw_stdout, raw_stderr, structured_result, duration_seconds,
    adapter_metadata, started_at, finished_at
)
SELECT
    id, ticket_id, NULL, role, mode, cycle_number, attempt_number,
    exit_status, verdict, prompt, run_request, diff_context,
    raw_stdout, raw_stderr, structured_result, duration_seconds,
    adapter_metadata, started_at, finished_at
FROM agent_runs;

DROP TABLE agent_runs;
ALTER TABLE agent_runs_new RENAME TO agent_runs;

CREATE INDEX IF NOT EXISTS idx_agent_runs_ticket_role
    ON agent_runs(ticket_id, role, started_at);

CREATE INDEX IF NOT EXISTS idx_agent_runs_epic
    ON agent_runs(epic_id, role, started_at);

-- ===================================================================
-- 3. Recreate state_transitions with nullable ticket_id, new epic_id, XOR check
-- ===================================================================

CREATE TABLE state_transitions_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     TEXT REFERENCES tickets(id),
    epic_id       TEXT REFERENCES planned_epics(id),
    from_status   TEXT NOT NULL,
    to_status     TEXT NOT NULL,
    triggered_by  TEXT NOT NULL,
    reason        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (
        (ticket_id IS NOT NULL AND epic_id IS NULL) OR
        (ticket_id IS NULL AND epic_id IS NOT NULL)
    )
);

INSERT INTO state_transitions_new (
    id, ticket_id, epic_id, from_status, to_status, triggered_by, reason, created_at
)
SELECT
    id, ticket_id, NULL, from_status, to_status, triggered_by, reason, created_at
FROM state_transitions;

DROP TABLE state_transitions;
ALTER TABLE state_transitions_new RENAME TO state_transitions;

CREATE INDEX IF NOT EXISTS idx_state_transitions_ticket
    ON state_transitions(ticket_id, created_at);

CREATE INDEX IF NOT EXISTS idx_state_transitions_epic
    ON state_transitions(epic_id, created_at);

-- ===================================================================
-- 4. Recreate decisions with nullable ticket_id, new epic_id, XOR check
-- ===================================================================

CREATE TABLE decisions_new (
    id          TEXT PRIMARY KEY,
    ticket_id   TEXT REFERENCES tickets(id),
    epic_id     TEXT REFERENCES planned_epics(id),
    decision    TEXT NOT NULL CHECK (decision IN (
                    'approve','reject','revise','defer','unblock'
                )),
    rationale   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (
        (ticket_id IS NOT NULL AND epic_id IS NULL) OR
        (ticket_id IS NULL AND epic_id IS NOT NULL)
    )
);

INSERT INTO decisions_new (
    id, ticket_id, epic_id, decision, rationale, created_at
)
SELECT
    id, ticket_id, NULL, decision, rationale, created_at
FROM decisions;

DROP TABLE decisions;
ALTER TABLE decisions_new RENAME TO decisions;

-- ===================================================================
-- 5. Extend orchestrator_state with loop_type and active_plan_id
-- ===================================================================

ALTER TABLE orchestrator_state ADD COLUMN active_plan_id TEXT REFERENCES planned_epics(id);
ALTER TABLE orchestrator_state ADD COLUMN loop_type TEXT CHECK (loop_type IN ('implementation', 'planning'));

-- ===================================================================
-- 6. Add lineage FK on tickets
-- ===================================================================

ALTER TABLE tickets ADD COLUMN planned_ticket_id TEXT REFERENCES planned_tickets(id);

-- ===================================================================
-- 7. Indexes for new planning tables
-- ===================================================================

CREATE INDEX IF NOT EXISTS idx_planned_epics_project_status
    ON planned_epics(project_id, status);

CREATE INDEX IF NOT EXISTS idx_planned_tickets_epic
    ON planned_tickets(epic_id, sequence);

CREATE INDEX IF NOT EXISTS idx_planning_findings_epic_disposition
    ON planning_findings(epic_id, disposition);

CREATE INDEX IF NOT EXISTS idx_planning_findings_run
    ON planning_findings(run_id);

CREATE INDEX IF NOT EXISTS idx_planning_findings_fingerprint
    ON planning_findings(epic_id, fingerprint, disposition);

CREATE INDEX IF NOT EXISTS idx_planned_ticket_criteria_ticket
    ON planned_ticket_criteria(planned_ticket_id);

CREATE INDEX IF NOT EXISTS idx_planned_ticket_deps_depends_on
    ON planned_ticket_dependencies(depends_on_id);
