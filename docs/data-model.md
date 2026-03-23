# Data Model

## Core Entities

### Implementation Loop

- `projects`
- `tickets`
- `acceptance_criteria`
- `ticket_dependencies`
- `agent_runs` (shared with planning)
- `run_diffs`
- `review_baselines`
- `orchestrator_state` (shared with planning)
- `findings`
- `decisions` (shared with planning)
- `state_transitions` (shared with planning)

### Planning Loop

- `planned_epics` — a planning brief and its draft/review state
- `planned_tickets` — individual ticket plans within an epic
- `planned_ticket_criteria` — acceptance criteria for planned tickets
- `planned_ticket_dependencies` — dependency edges between planned tickets
- `planning_findings` — review findings against planning artifacts
- `materialization_hashes` — content hashes for materialized files

### Shared Tables

`agent_runs`, `decisions`, and `state_transitions` are shared between the
implementation and planning loops. Each has a nullable `ticket_id` and
`epic_id` with an XOR constraint: exactly one must be set per row.

`orchestrator_state` is shared via a `loop_type` discriminator column
(`'implementation'` or `'planning'`) and `active_plan_id` alongside the
existing `active_ticket_id`.

### Lineage

`tickets.planned_ticket_id` links a materialized implementation ticket back
to the planned ticket it was generated from (1:1 relationship).

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
                    'empty_implementation','low_confidence_pass',
                    'permission_denied'
                )),
    status      TEXT NOT NULL DEFAULT 'ready'
                CHECK (status IN (
                    'ready','implementing','in-review',
                    'revise','human-gate','pr-ready',
                    'blocked','done'
                )),
    planned_ticket_id TEXT UNIQUE REFERENCES planned_tickets(id),
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
    active_plan_id   TEXT REFERENCES planned_epics(id),
    active_run_id    TEXT REFERENCES agent_runs(id),
    loop_type        TEXT CHECK (loop_type IN ('implementation', 'planning')),
    status           TEXT NOT NULL DEFAULT 'idle'
                     CHECK (status IN ('idle','running','awaiting_human','suspended')),
    suspended_at     TEXT,
    resume_context   TEXT,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project_id)
);

CREATE TABLE state_transitions (
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

CREATE TABLE decisions (
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

-- Planning tables

CREATE TABLE planned_epics (
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

CREATE TABLE planned_tickets (
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

CREATE TABLE planned_ticket_criteria (
    id                TEXT PRIMARY KEY,
    planned_ticket_id TEXT NOT NULL REFERENCES planned_tickets(id),
    description       TEXT NOT NULL
);

CREATE TABLE planned_ticket_dependencies (
    planned_ticket_id TEXT NOT NULL REFERENCES planned_tickets(id),
    depends_on_id     TEXT NOT NULL REFERENCES planned_tickets(id),
    PRIMARY KEY (planned_ticket_id, depends_on_id),
    CHECK (planned_ticket_id != depends_on_id)
);

CREATE TABLE planning_findings (
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

CREATE TABLE materialization_hashes (
    epic_id           TEXT NOT NULL REFERENCES planned_epics(id),
    planned_ticket_id TEXT REFERENCES planned_tickets(id),
    file_path         TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    materialized_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (epic_id, file_path)
);
```

## Lineage

`tickets.planned_ticket_id` is a nullable FK to `planned_tickets(id)`. When
a planned ticket is materialized into an implementation ticket, this column
links the two. The relationship is 1:1.

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
- `planning_findings.fingerprint` is computed as
  `(category, target_type, target_sequence, description_prefix)` where
  `description_prefix` is the first 80 characters of the description,
  normalized to lowercase with collapsed whitespace
- `planned_tickets.scope`, `non_goals`, `references_`, and
  `implementation_notes` are stored as JSON arrays of strings
- `orchestrator_state.loop_type` is `NULL` when idle, `'implementation'` when
  running the implementation loop, and `'planning'` when running the planning
  loop; `active_ticket_id` and `active_plan_id` follow the same mutual
  exclusion as the loop type

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

-- Planning indexes

CREATE INDEX idx_planned_epics_project_status
    ON planned_epics(project_id, status);

CREATE INDEX idx_planned_tickets_epic
    ON planned_tickets(epic_id, sequence);

CREATE INDEX idx_planning_findings_epic_disposition
    ON planning_findings(epic_id, disposition);

CREATE INDEX idx_planning_findings_run
    ON planning_findings(run_id);

CREATE INDEX idx_planning_findings_fingerprint
    ON planning_findings(epic_id, fingerprint, disposition);

CREATE INDEX idx_planned_ticket_criteria_ticket
    ON planned_ticket_criteria(planned_ticket_id);

CREATE INDEX idx_planned_ticket_deps_depends_on
    ON planned_ticket_dependencies(depends_on_id);

CREATE INDEX idx_agent_runs_epic
    ON agent_runs(epic_id, role, started_at);

CREATE INDEX idx_state_transitions_epic
    ON state_transitions(epic_id, created_at);
```

## SQLite Notes

- every connection should enable `PRAGMA foreign_keys = ON`
- even though SQLite is permissive about FK declaration order, migrations should
  still create tables in dependency order
