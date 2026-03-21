# Data Model

## Core Entities

The implementation loop uses entities along these lines:

- `projects`
- `tickets`
- `acceptance_criteria`
- `ticket_dependencies`
- `agent_runs`
- `run_diffs`
- `review_baselines`
- `orchestrator_state`
- `findings`
- `decisions`
- `state_transitions`

Planning entities such as `epics` and outward-facing entities such as
`exports` can be added after the core loop is validated.

## SQLite Schema

```sql
CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    repo_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    config      TEXT
);

CREATE TABLE tickets (
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

CREATE TABLE acceptance_criteria (
    id          TEXT PRIMARY KEY,
    ticket_id   TEXT NOT NULL REFERENCES tickets(id),
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','met','unmet','disputed')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE ticket_dependencies (
    ticket_id      TEXT NOT NULL REFERENCES tickets(id),
    depends_on_id  TEXT NOT NULL REFERENCES tickets(id),
    PRIMARY KEY (ticket_id, depends_on_id),
    CHECK (ticket_id != depends_on_id)
);

CREATE TABLE agent_runs (
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

CREATE TABLE run_diffs (
    run_id         TEXT PRIMARY KEY REFERENCES agent_runs(id),
    diff_text      TEXT NOT NULL,
    files_changed  TEXT NOT NULL
);

CREATE TABLE review_baselines (
    run_id            TEXT PRIMARY KEY REFERENCES agent_runs(id),
    baseline_diff     TEXT NOT NULL,
    baseline_status   TEXT NOT NULL,
    post_diff         TEXT,
    violation         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE findings (
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

CREATE TABLE orchestrator_state (
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

CREATE TABLE state_transitions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     TEXT NOT NULL REFERENCES tickets(id),
    from_status   TEXT NOT NULL,
    to_status     TEXT NOT NULL,
    triggered_by  TEXT NOT NULL,
    reason        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE decisions (
    id          TEXT PRIMARY KEY,
    ticket_id   TEXT NOT NULL REFERENCES tickets(id),
    decision    TEXT NOT NULL CHECK (decision IN (
                    'approve','reject','revise','defer','unblock'
                )),
    rationale   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Persistence Notes

- `agent_runs.structured_result` stores the review result payload defined in
  [adapters.md](./adapters.md)
- use ULIDs for text primary keys that appear in envelopes, logs, and rendered
  reports; use the `python-ulid` package
- keep `structured_result` and `adapter_metadata` as JSON blobs rather than
  over-normalizing early
- store the fully serialized run request in `agent_runs.run_request`
- denormalize reviewer verdict onto `agent_runs.verdict` for cheap loop-control
  queries
- `findings.acceptance_criterion_id` links a finding to the specific criterion
  it relates to, enabling mechanical criterion-status updates
- `findings.fingerprint` is computed as
  `(category, location, description_prefix)` by the orchestrator at persistence
  time and used for cross-cycle reconciliation; `description_prefix` is the
  first 80 characters of the description, normalized to lowercase with
  collapsed whitespace
- use `agent_runs.exit_status = 'running'` as the orchestrator-owned marker for
  in-flight runs before final status is known
- keep large diffs in `run_diffs` instead of embedding them directly in
  `agent_runs`
- persist reviewer baseline comparisons in `review_baselines`
- treat reviewer runs as `agent_runs` with `role = 'reviewer'`
- create tables in foreign-key dependency order in the migration
- keep acceptance-criteria status only on `acceptance_criteria`
- `projects.config` stores a snapshot of the parsed config loaded at init or
  startup; `config.toml` on disk is the source of truth and the DB snapshot is
  refreshed on each command invocation
- `activity.log` is an append-only debug trace for operators; it is not
  canonical state

## Recommended Indexes

```sql
CREATE INDEX idx_tickets_project_status
    ON tickets(project_id, status);

CREATE INDEX idx_agent_runs_ticket_role
    ON agent_runs(ticket_id, role, started_at);

CREATE INDEX idx_findings_ticket_disposition
    ON findings(ticket_id, disposition);

CREATE INDEX idx_findings_run
    ON findings(run_id);

CREATE INDEX idx_findings_criterion
    ON findings(acceptance_criterion_id);

CREATE INDEX idx_findings_fingerprint
    ON findings(ticket_id, fingerprint, disposition);

CREATE INDEX idx_state_transitions_ticket
    ON state_transitions(ticket_id, created_at);

CREATE INDEX idx_acceptance_criteria_ticket
    ON acceptance_criteria(ticket_id);

CREATE INDEX idx_ticket_deps_depends_on
    ON ticket_dependencies(depends_on_id);

CREATE INDEX idx_orchestrator_state_active_ticket
    ON orchestrator_state(active_ticket_id);
```

## SQLite Notes

- every connection should enable `PRAGMA foreign_keys = ON`
- even though SQLite is permissive about FK declaration order, migrations should
  still create tables in dependency order
