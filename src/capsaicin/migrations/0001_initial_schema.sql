-- MVP schema: all 11 tables and 10 indexes from data-model.md
-- Tables are created in FK dependency order.

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    repo_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    config      TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
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
                    'empty_implementation','low_confidence_pass'
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

CREATE TABLE IF NOT EXISTS acceptance_criteria (
    id          TEXT PRIMARY KEY,
    ticket_id   TEXT NOT NULL REFERENCES tickets(id),
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','met','unmet','disputed')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ticket_dependencies (
    ticket_id      TEXT NOT NULL REFERENCES tickets(id),
    depends_on_id  TEXT NOT NULL REFERENCES tickets(id),
    PRIMARY KEY (ticket_id, depends_on_id),
    CHECK (ticket_id != depends_on_id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id                TEXT PRIMARY KEY,
    ticket_id         TEXT NOT NULL REFERENCES tickets(id),
    role              TEXT NOT NULL CHECK (role IN ('implementer','reviewer','planner','human')),
    mode              TEXT NOT NULL CHECK (mode IN ('read-write','read-only')),
    cycle_number      INTEGER NOT NULL,
    attempt_number    INTEGER NOT NULL DEFAULT 1,
    exit_status       TEXT NOT NULL CHECK (exit_status IN (
                          'running','success','failure','timeout',
                          'contract_violation','parse_error'
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

CREATE TABLE IF NOT EXISTS run_diffs (
    run_id         TEXT PRIMARY KEY REFERENCES agent_runs(id),
    diff_text      TEXT NOT NULL,
    files_changed  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_baselines (
    run_id            TEXT PRIMARY KEY REFERENCES agent_runs(id),
    baseline_diff     TEXT NOT NULL,
    baseline_status   TEXT NOT NULL,
    post_diff         TEXT,
    violation         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS findings (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES agent_runs(id),
    ticket_id    TEXT NOT NULL REFERENCES tickets(id),
    acceptance_criterion_id TEXT REFERENCES acceptance_criteria(id),
    severity     TEXT NOT NULL CHECK (severity IN ('blocking','warning','info')),
    category     TEXT NOT NULL,
    location     TEXT,
    fingerprint  TEXT NOT NULL,
    description  TEXT NOT NULL,
    disposition  TEXT NOT NULL DEFAULT 'open'
                 CHECK (disposition IN ('open','fixed','wont_fix','disputed')),
    resolved_in_run TEXT REFERENCES agent_runs(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orchestrator_state (
    project_id       TEXT NOT NULL REFERENCES projects(id),
    active_ticket_id TEXT REFERENCES tickets(id),
    active_run_id    TEXT REFERENCES agent_runs(id),
    status           TEXT NOT NULL DEFAULT 'idle'
                     CHECK (status IN ('idle','running','awaiting_human','suspended')),
    suspended_at     TEXT,
    resume_context   TEXT,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project_id)
);

CREATE TABLE IF NOT EXISTS state_transitions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     TEXT NOT NULL REFERENCES tickets(id),
    from_status   TEXT NOT NULL,
    to_status     TEXT NOT NULL,
    triggered_by  TEXT NOT NULL,
    reason        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS decisions (
    id          TEXT PRIMARY KEY,
    ticket_id   TEXT NOT NULL REFERENCES tickets(id),
    decision    TEXT NOT NULL CHECK (decision IN (
                    'approve','reject','revise','defer','unblock'
                )),
    rationale   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes from data-model.md

CREATE INDEX IF NOT EXISTS idx_tickets_project_status
    ON tickets(project_id, status);

CREATE INDEX IF NOT EXISTS idx_agent_runs_ticket_role
    ON agent_runs(ticket_id, role, started_at);

CREATE INDEX IF NOT EXISTS idx_findings_ticket_disposition
    ON findings(ticket_id, disposition);

CREATE INDEX IF NOT EXISTS idx_findings_run
    ON findings(run_id);

CREATE INDEX IF NOT EXISTS idx_findings_criterion
    ON findings(acceptance_criterion_id);

CREATE INDEX IF NOT EXISTS idx_findings_fingerprint
    ON findings(ticket_id, fingerprint, disposition);

CREATE INDEX IF NOT EXISTS idx_state_transitions_ticket
    ON state_transitions(ticket_id, created_at);

CREATE INDEX IF NOT EXISTS idx_acceptance_criteria_ticket
    ON acceptance_criteria(ticket_id);

CREATE INDEX IF NOT EXISTS idx_ticket_deps_depends_on
    ON ticket_dependencies(depends_on_id);

CREATE INDEX IF NOT EXISTS idx_orchestrator_state_active_ticket
    ON orchestrator_state(active_ticket_id);
