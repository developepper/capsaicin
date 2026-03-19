# Implementation Plan

## Phased Roadmap

| Phase | Focus | Tickets |
|-------|-------|---------|
| 1 | Foundation | T01–T04 |
| 2 | Project & Ticket Management | T05–T07 |
| 3 | State Machine & Orchestrator | T08–T09 |
| 4 | Adapter Layer | T10–T13 |
| 5 | Implementation Pipeline | T14–T15 |
| 6 | Review Pipeline | T16–T20 |
| 7 | Human Gate & Decisions | T21–T23 |
| 8 | Status & Recovery | T24–T26 |
| 9 | Automated Loop | T27 |

## Epic: Implementation-Loop MVP

Build the end-to-end implementation loop: init a project, add tickets, run
implementer, run reviewer, handle human gates, and drive the bounded
implement-review-revise cycle to completion.

---

### T01: Python project scaffolding

**Goal**: Runnable `capsaicin` CLI entrypoint with no commands yet.

**Scope**:
- `pyproject.toml` with project metadata, Python >=3.11, dependencies (click,
  tomli/tomllib)
- `src/capsaicin/` package layout
- `src/capsaicin/cli.py` with a click group entrypoint
- `capsaicin` console_scripts entrypoint
- basic test that the CLI prints help

**Non-goals**: No commands, no database, no config loading.

**Acceptance criteria**:
- `capsaicin --help` runs and prints usage
- `pip install -e .` succeeds
- `pytest` discovers and passes at least one test

**Dependencies**: None.

**Notes**: Use `click` over `typer` — click has better support for nested
subcommand groups (`ticket add`, `ticket run`, etc.) and does not require
type-annotation gymnastics. Use `tomllib` (stdlib in 3.11+) for TOML.

---

### T02: Database module

**Goal**: Reusable SQLite connection factory and migration runner.

**Scope**:
- `src/capsaicin/db.py`
- `get_connection(db_path)` that returns a connection with
  `PRAGMA foreign_keys = ON` enabled
- `run_migrations(connection)` that executes SQL from a migrations directory or
  embedded string
- migrations are idempotent (use `CREATE TABLE IF NOT EXISTS` or a version
  table)
- unit tests for FK enforcement and migration idempotency

**Non-goals**: No schema definition yet. No config integration.

**Acceptance criteria**:
- `get_connection` returns a connection where FK violations raise errors
- `run_migrations` can be called twice without error
- tests pass against an in-memory SQLite database

**Dependencies**: T01.

---

### T03: Schema migration

**Goal**: Full MVP schema and indexes in a runnable migration.

**Scope**:
- migration SQL containing all tables from `data-model.md`: `projects`,
  `tickets`, `acceptance_criteria`, `ticket_dependencies`, `agent_runs`,
  `run_diffs`, `review_baselines`, `findings`, `orchestrator_state`,
  `state_transitions`, `decisions`
- all indexes from `data-model.md`
- tables created in FK dependency order
- test that all tables exist after migration
- test that FK constraints are enforced (e.g., inserting a ticket with a
  nonexistent project_id fails)

**Non-goals**: No data access layer. No ORM.

**Acceptance criteria**:
- migration creates all 11 tables and 10 indexes
- CHECK constraints are enforced (e.g., invalid status values rejected)
- FK constraints are enforced
- migration is idempotent

**Dependencies**: T02.

---

### T04: Config loading

**Goal**: Parse `config.toml`, validate required fields, expose typed config.

**Scope**:
- `src/capsaicin/config.py`
- `load_config(config_path) -> Config` dataclass/dict
- validate required sections: `[project]`, `[adapters.implementer]`,
  `[adapters.reviewer]`, `[limits]`
- apply defaults for optional fields (`timeout_seconds`, adapter tool defaults,
  etc.)
- `resolve_project(capsaicin_root)` that implements single-project auto-resolve
  or errors on ambiguity
- `write_default_config(path, project_name, repo_path)` for init
- unit tests for missing fields, defaults, project resolution

**Non-goals**: No DB snapshot sync yet (that happens in init/command
entrypoints). No adapter-specific validation.

**Acceptance criteria**:
- valid config.toml parses into a structured Config
- missing required sections raise clear errors
- defaults are applied for omitted optional fields
- single-project resolution works; multiple projects error without explicit
  selection

**Dependencies**: T01.

---

### T05: `capsaicin init`

**Goal**: Initialize a project with directory structure, database, and config.

**Scope**:
- `capsaicin init [--project NAME] [--repo PATH]`
- create `.capsaicin/projects/<slug>/` directory tree
- create `capsaicin.db` and run migrations
- write default `config.toml` with resolved absolute repo path
- insert `projects` row with config snapshot in `projects.config`
- insert `orchestrator_state` row with `status = 'idle'`
- create `activity.log` as an append-only debug trace file
- create `renders/` and `exports/` directories
- slug generation from project name (lowercase, hyphens, strip special chars)
- error if project already exists

**Non-goals**: No ticket commands. No adapter validation.

**Acceptance criteria**:
- directory structure matches `architecture.md` layout
- `capsaicin.db` exists with all tables
- `config.toml` exists with correct defaults and absolute repo_path
- `projects` row exists in DB
- `orchestrator_state` row exists with `idle` status
- `activity.log` exists
- re-running init for the same project errors cleanly

**Dependencies**: T02, T03, T04.

---

### T06: `capsaicin ticket add`

**Goal**: Create tickets with acceptance criteria via CLI args or TOML file.

**Scope**:
- `capsaicin ticket add --title TITLE --description DESC [--criteria "..."]`
- `capsaicin ticket add --from FILE` (TOML format from cli.md)
- generate ULID for ticket and each criterion
- insert ticket in `ready` status
- insert acceptance criteria in `pending` status
- record state transition (`null -> ready`)
- print ticket ID and brief summary to stdout

**Non-goals**: No dependency management. No rendered file output to `renders/`
(stdout only, per updated CLI spec). No batch import.

**Acceptance criteria**:
- inline creation produces a ticket row with correct fields
- file import parses the TOML format and creates ticket + criteria
- criteria are linked to the ticket by FK
- state transition is recorded
- ticket ID is printed to stdout for use in subsequent commands
- missing required fields error clearly

**Dependencies**: T05.

**Notes**: Use the `python-ulid` package for ID generation. ULIDs are preferred
over UUIDs per data-model.md because they are sortable and more readable in
logs.

---

### T07: `capsaicin ticket dep`

**Goal**: Add ticket dependencies with cycle detection.

**Scope**:
- `capsaicin ticket dep TICKET_ID --on DEPENDENCY_ID`
- validate both tickets exist
- detect cycles via DFS/BFS on the dependency graph before writing
- insert dependency edge if valid
- print confirmation

**Non-goals**: No dependency visualization. No bulk dependency import.

**Acceptance criteria**:
- valid dependency is inserted
- nonexistent ticket IDs error clearly
- self-dependency is rejected (enforced by CHECK constraint)
- circular dependency is rejected (A->B->C->A detected before write)
- duplicate dependency is handled gracefully

**Dependencies**: T06.

---

### T08: State machine module

**Goal**: Enforce ticket transition rules as a reusable module.

**Scope**:
- `src/capsaicin/state_machine.py`
- `transition_is_legal(from_status, to_status, actor) -> bool`
- `LEGAL_TRANSITIONS` table encoding all rules from `state-machine.md`
- `transition_ticket(conn, ticket_id, to_status, triggered_by, reason)` that
  validates legality, updates `tickets.status` and `status_changed_at`, records
  a `state_transitions` row, and updates `gate_reason`/`blocked_reason` when
  applicable
- dependency satisfaction check for `ready -> implementing`
- unit tests for every legal transition and every important illegal transition

**Non-goals**: No orchestrator state updates. No retry/cycle counter management
(that's T09).

**Acceptance criteria**:
- all legal transitions from `state-machine.md` are accepted
- `revise -> pr-ready` is rejected
- transitions without `human-gate` before `pr-ready` are rejected
- `ready -> implementing` with unmet dependencies is rejected
- each transition creates a `state_transitions` row
- `gate_reason` and `blocked_reason` are set correctly per guard conditions

**Dependencies**: T03.

---

### T09: Orchestrator state management

**Goal**: Track active ticket/run and manage cycle/retry counters.

**Scope**:
- `src/capsaicin/orchestrator.py`
- `start_run(conn, project_id, ticket_id, run_id)` — set active ticket/run,
  status = running
- `finish_run(conn, project_id)` — clear active run
- `await_human(conn, project_id)` — set status = awaiting_human
- `set_idle(conn, project_id)` — set status = idle
- cycle/retry counter helpers:
  - `init_cycle(conn, ticket_id)` — set cycle=1, reset attempts
  - `increment_cycle(conn, ticket_id)` — increment cycle, reset impl_attempt
  - `increment_impl_attempt(conn, ticket_id)`
  - `increment_review_attempt(conn, ticket_id)`
  - `check_cycle_limit(conn, ticket_id, max_cycles) -> bool`
  - `check_impl_retry_limit(conn, ticket_id, max_retries) -> bool`
  - `check_review_retry_limit(conn, ticket_id, max_retries) -> bool`
- unit tests for counter behavior and state updates

**Non-goals**: No resume logic. No run invocation.

**Acceptance criteria**:
- orchestrator state transitions between idle/running/awaiting_human correctly
- cycle counter increments and resets work as specified
- retry counters are independent of cycle counters
- limit checks return correct booleans at boundary values

**Dependencies**: T03.

---

### T10: Adapter contract and types

**Goal**: Define RunRequest, RunResult, ReviewResult types and base adapter
interface.

**Scope**:
- `src/capsaicin/adapters/types.py` — dataclasses for RunRequest, RunResult,
  ReviewResult, Finding (as defined in adapters.md envelopes and schemas)
- `src/capsaicin/adapters/base.py` — abstract BaseAdapter with
  `execute(request: RunRequest) -> RunResult`
- JSON serialization/deserialization for all types
- unit tests for round-trip serialization and type validation

**Non-goals**: No concrete adapter implementation. No prompt assembly.

**Acceptance criteria**:
- RunRequest can represent both implementer and reviewer invocations
- RunResult can carry structured_result as optional ReviewResult
- ReviewResult validates verdict/finding/confidence constraints from adapters.md
- all types serialize to/from JSON cleanly

**Dependencies**: T01.

---

### T11: Prompt assembly

**Goal**: Build implementer and reviewer prompts from ticket context.

**Scope**:
- `src/capsaicin/prompts.py`
- `build_implementer_prompt(ticket, criteria, prior_findings, cycle, max_cycles)
  -> str`
- `build_reviewer_prompt(ticket, criteria, diff_context, prior_findings) -> str`
- implementer prompt includes: role instruction, ticket title/description,
  criteria with statuses, prior findings (when revising), cycle info, scope
  constraint
- reviewer prompt includes: independent reviewer role instruction, diff,
  ticket context, criteria, prior findings, JSON schema-constrained output
  instruction, anti-bias instruction
- unit tests that prompts contain required elements

**Non-goals**: No template customization. No adapter-specific formatting.

**Acceptance criteria**:
- implementer prompt contains all required elements from cli.md:121-127
- reviewer prompt contains all required elements from cli.md:161-167
- reviewer prompt includes explicit JSON schema for expected output
- reviewer prompt includes anti-bias instruction
- prior findings are included only when non-empty

**Dependencies**: T10.

---

### T12: Claude Code adapter — implementer mode

**Goal**: Invoke Claude Code as an implementer via subprocess.

**Scope**:
- `src/capsaicin/adapters/claude_code.py`
- `ClaudeCodeAdapter(BaseAdapter)` with implementer execution path
- subprocess invocation with `--print`, `--output-format json`, timeout
- capture stdout, stderr, exit code, duration
- map exit codes to RunResult exit_status (success/failure/timeout)
- adapter_metadata: session_id, turns, cost, usage, modelUsage, and
  permission_denials when available
- integration tests use mocked subprocess behavior plus captured real Claude
  JSON envelopes in `tests/fixtures/`
- integration test with a mock/stub subprocess (do not require real Claude Code
  for unit tests)

**Non-goals**: No reviewer mode. No structured result parsing.

**Acceptance criteria**:
- adapter constructs correct subprocess command from RunRequest
- timeout is enforced via subprocess timeout
- successful run returns exit_status=success with captured stdout/stderr
- failed run returns exit_status=failure
- timed-out run returns exit_status=timeout
- adapter_metadata is populated from JSON output when available

**Dependencies**: T10, T11.

**Notes**: Validated Claude Code implementer invocation is
`claude -p --output-format json -- "PROMPT"`. Parse the outer JSON envelope and
use `result` as the assistant text payload.

---

### T13: Claude Code adapter — reviewer mode

**Goal**: Invoke Claude Code as a reviewer with structured result extraction.

**Scope**:
- extend `ClaudeCodeAdapter` with reviewer execution path
- invoke with read-only tool constraints via Claude Code `--allowed-tools`
- pass the prompt after `--` so it is not parsed as additional tool names
- provide `--json-schema` so the review result is returned in the outer JSON
  envelope's `structured_output` field
- pass the full MVP Review Result Schema from adapters.md, not a simplified
  summary schema
- parse `structured_output` into ReviewResult type
- fall back to parsing `result` only if `structured_output` is absent
- validate per adapters.md rules (verdict/finding consistency, confidence
  checks, criterion ID validity)
- treat JSON Schema compliance as necessary but not sufficient; semantic
  contract validation still determines whether the result is accepted
- return exit_status=parse_error when extraction or validation fails,
  preserving raw output
- integration tests use captured real Claude Code reviewer JSON envelopes in
  `tests/fixtures/`

**Non-goals**: No review baseline checking (that's the orchestrator's job). No
finding reconciliation.

**Acceptance criteria**:
- reviewer invocation includes read-only tool constraints via `--allowed-tools`
- reviewer invocation passes the prompt after `--`
- structured result is read from the JSON output envelope's
  `structured_output` field
- valid ReviewResult passes validation
- verdict:fail without blocking findings returns parse_error
- verdict:pass with blocking findings returns parse_error
- confidence:high with empty files_examined returns parse_error
- invalid criterion_id references return parse_error
- raw output is preserved on parse_error for debugging

**Dependencies**: T10, T11.

---

### T14: Diff capture module

**Goal**: Capture tracked-file diffs and persist them.

**Scope**:
- `src/capsaicin/diff.py`
- `capture_diff(repo_path) -> DiffResult` — run `git diff HEAD`, return diff
  text and list of changed files
- `persist_run_diff(conn, run_id, diff_result)` — insert into `run_diffs`
- `get_run_diff(conn, run_id) -> DiffResult` — retrieve persisted diff
- `diffs_match(a, b) -> bool` — compare two diff texts for workspace drift
  detection
- unit tests with a temporary git repo

**Non-goals**: No untracked file handling. No review baseline (that's T16).

**Acceptance criteria**:
- `capture_diff` returns correct diff text for modified tracked files
- empty diff is detected (no changes)
- diff is persisted and retrievable by run_id
- `diffs_match` correctly identifies matching and divergent diffs

**Dependencies**: T03.

---

### T15: `capsaicin ticket run`

**Goal**: Full implementation pipeline from ticket selection through post-run
state transition.

**Scope**:
- `capsaicin ticket run [TICKET_ID]`
- auto-select next ready ticket if no ID provided (ordered by created_at,
  dependencies satisfied)
- cycle-limit check: if in `revise` and `current_cycle >= max_cycles`, go
  directly to `human-gate` with `gate_reason = 'cycle_limit'`
- transition to `implementing`
- update orchestrator state (active ticket/run, status=running)
- init or increment cycle counter
- assemble implementer RunRequest via prompt builder
- insert agent_run with exit_status=running
- invoke implementer adapter
- update agent_run with terminal status
- capture post-run diff
- if success + non-empty diff: persist run_diffs, transition to `in-review`
- if success + empty diff: transition to `human-gate` with
  `gate_reason = 'empty_implementation'`
- if failure/timeout: increment retry, retry or transition to `blocked`
- update orchestrator state appropriately
- integration test with mock adapter

**Non-goals**: No review invocation. No loop automation.

**Acceptance criteria**:
- auto-selection picks correct ticket respecting dependencies and ordering
- cycle-limit shortcut to human-gate works without invoking adapter
- successful run with changes transitions to in-review
- successful run without changes transitions to human-gate
- failed run increments retry counter
- retry limit exceeded transitions to blocked
- agent_run row is inserted before invocation and updated after
- run_diffs row is created on success with non-empty diff
- orchestrator_state reflects active ticket/run during execution
- state_transitions rows are recorded

**Dependencies**: T08, T09, T12, T14.

---

### T16: Review baseline and workspace drift check

**Goal**: Capture review baselines and enforce workspace-drift policy.

**Scope**:
- `src/capsaicin/review_baseline.py`
- `check_workspace_drift(conn, repo_path, run_id) -> DriftResult` — compare
  current `git diff HEAD` against persisted `run_diffs.diff_text`
- `capture_review_baseline(conn, repo_path, run_id)` — snapshot pre-review
  tracked-file state into `review_baselines`
- `check_review_violation(conn, repo_path, run_id) -> bool` — compare
  post-review state to baseline, return True if reviewer modified tracked files
- `handle_drift(conn, run_id, repo_path, allow_drift)` — if drift detected and
  allow_drift=True, re-capture run_diffs; if drift detected and
  allow_drift=False, raise error
- unit tests with temporary git repos

**Non-goals**: No review invocation. No result validation.

**Acceptance criteria**:
- drift is detected when workspace differs from captured implementation diff
- no-drift case passes cleanly
- `--allow-drift` re-captures the diff as new baseline
- review violation is detected when reviewer modifies tracked files
- no-violation case passes cleanly
- review_baselines rows are created and compared correctly

**Dependencies**: T14.

---

### T17: Review result validation

**Goal**: Validate parsed ReviewResult per adapters.md rules.

**Scope**:
- `src/capsaicin/validation.py`
- `validate_review_result(result: ReviewResult, criteria_ids: list[str]) ->
  ValidationResult`
- rules from adapters.md:
  - verdict:fail requires >=1 blocking finding
  - verdict:pass cannot have blocking findings
  - confidence:high invalid with empty files_examined
  - confidence:high invalid with criteria provided but criteria_checked empty
  - criteria_checked entries must reference valid criterion IDs
  - acceptance_criterion_id on findings must reference valid criterion IDs
  - all top-level fields must be present
- ValidationResult includes pass/fail and list of violation descriptions
- unit tests for every rule

**Non-goals**: No finding persistence. No AC updates.

**Acceptance criteria**:
- each validation rule is individually testable and tested
- valid results pass
- each invalid case produces a specific violation description
- validation is pure (no DB access)

**Dependencies**: T10.

---

### T18: Acceptance criteria updates

**Goal**: Update criterion statuses based on review results.

**Scope**:
- `src/capsaicin/criteria.py`
- `update_criteria_from_review(conn, ticket_id, review_result)` — apply the
  AC update rule from cli.md:189-200
- match criteria_checked entries by criterion_id
- match findings to criteria by acceptance_criterion_id
- checked criterion + blocking finding with matching AC ID → unmet
- checked criterion + no blocking finding → met
- unchecked criterion → unchanged
- unit tests with various combinations

**Non-goals**: No finding persistence. No reconciliation.

**Acceptance criteria**:
- criterion correctly marked `met` when checked with no blocking finding
- criterion correctly marked `unmet` when checked with blocking finding
- criterion left unchanged when not checked
- findings with null acceptance_criterion_id do not affect any criterion
- multiple criteria updated correctly in one pass

**Dependencies**: T10, T03.

---

### T19: Finding reconciliation

**Goal**: Reconcile findings across review cycles using fingerprinting.

**Scope**:
- `src/capsaicin/reconciliation.py`
- `compute_fingerprint(category, location, description) -> str` — first 80
  chars of description, lowercased, whitespace-collapsed, combined with
  category and location
- `reconcile_findings(conn, ticket_id, impl_run_id, new_findings,
  is_first_cycle)`:
  - first cycle: persist all as new
  - verdict pass: bulk-close all prior open findings with
    resolved_in_run=impl_run_id
  - verdict fail: match by fingerprint, update matched, close unmatched prior,
    create unmatched new
- unit tests for each reconciliation path

**Non-goals**: No semantic matching. No human disposition overrides (those
happen at human-gate).

**Acceptance criteria**:
- fingerprint is deterministic for same inputs
- fingerprints differ when description_prefix differs even with same
  category/location
- first-cycle findings are all persisted as new with generated IDs
- pass verdict bulk-closes all prior open findings
- fail verdict correctly matches, closes unmatched prior, creates new
- matched findings update description and severity but preserve original ID

**Dependencies**: T03, T10.

---

### T20: `capsaicin ticket review`

**Goal**: Full review pipeline integrating drift check, adapter invocation,
validation, AC updates, and finding reconciliation.

**Scope**:
- `capsaicin ticket review [TICKET_ID] [--allow-drift]`
- find ticket in `in-review`
- workspace drift check (T16), reject or re-capture
- capture review baseline (T16)
- update orchestrator state
- assemble reviewer RunRequest
- insert agent_run with exit_status=running
- invoke reviewer adapter (T13)
- update agent_run with terminal status
- post-review baseline violation check (T16)
- if violation: mark contract_violation, discard findings, retry or block
- parse and validate review result (T17)
- if parse/validation fails: mark parse_error, retry or block
- on valid fail: reconcile findings (T19), update AC (T18), transition to
  `revise`
- on valid pass: reconcile findings (T19) to bulk-close prior open findings,
  update AC (T18) to mark checked criteria as met, transition to `human-gate`
  with `gate_reason = 'review_passed'` (high/medium confidence) or
  `gate_reason = 'low_confidence_pass'` (low confidence)
- on valid escalate: transition to `human-gate` with
  `gate_reason = 'reviewer_escalated'`
- cycle-limit check: prefer human-gate with `gate_reason = 'cycle_limit'`
- update orchestrator state appropriately
- integration test with mock adapter

**Non-goals**: No loop automation. No human-gate commands.

**Acceptance criteria**:
- drift is rejected without --allow-drift
- drift is accepted with --allow-drift and diff re-captured
- contract violation from reviewer modifying files is detected and handled
- parse_error is handled with retry/block
- valid fail persists findings, updates AC, transitions to revise
- valid pass bulk-closes prior open findings via reconciliation
- valid pass updates checked criteria to met via AC update
- valid pass transitions to human-gate with correct gate_reason
- low confidence pass transitions to human-gate with low_confidence_pass
- escalate transitions to human-gate with reviewer_escalated
- review retry limit exceeded transitions to blocked
- all orchestrator/state_transition records are correct

**Dependencies**: T13, T15, T16, T17, T18, T19.

---

### T21: `capsaicin ticket approve`

**Goal**: Human approval gate with workspace verification.

**Scope**:
- `capsaicin ticket approve [TICKET_ID] [--rationale TEXT] [--force]`
- find ticket in `human-gate`
- verify workspace matches reviewed diff; reject unless `--force`
- require rationale when gate_reason is `cycle_limit`,
  `reviewer_escalated`, or `low_confidence_pass`
- record `approve` decision with rationale
- transition to `pr-ready`
- set orchestrator status to `idle`
- print PR preparation summary (ticket title, status, criteria, finding
  summary)

**Non-goals**: No PR creation. No rendered file output.

**Acceptance criteria**:
- approval succeeds when workspace matches and gate_reason is review_passed
- approval requires rationale for cycle_limit/reviewer_escalated/low_confidence
- approval without rationale for those gate_reasons errors
- workspace drift rejects approval without --force
- --force overrides workspace drift check
- decision row is recorded
- ticket transitions to pr-ready
- orchestrator state is idle

**Notes**: `pr-ready` is a terminal human-handoff state in MVP. PR creation and
merge remain manual.

**Dependencies**: T08, T09, T14.

---

### T22: `capsaicin ticket revise`

**Goal**: Human-initiated revision with optional findings and cycle reset.

**Scope**:
- `capsaicin ticket revise [TICKET_ID] [--add-finding DESCRIPTION]
  [--reset-cycles]`
- find ticket in `human-gate`
- optionally add human-supplied findings: create a synthetic `agent_run` with
  `role='human'`, `mode='read-write'`, `exit_status='success'`, then attach
  findings to that run (severity=blocking, category='human_feedback')
- record `revise` decision
- transition to `revise`
- optionally reset cycle and retry counters
- set orchestrator status to `idle`

**Non-goals**: No automatic re-implementation.

**Acceptance criteria**:
- revise transitions from human-gate to revise
- human findings are persisted with correct fields attached to a synthetic
  human agent_run
- the synthetic run has role='human' and is linked to the ticket
- decision row is recorded
- --reset-cycles resets counters
- without --reset-cycles, counters are preserved
- orchestrator state is idle

**Dependencies**: T08, T09.

---

### T23: `capsaicin ticket defer`

**Goal**: Defer or abandon a ticket from human-gate.

**Scope**:
- `capsaicin ticket defer [TICKET_ID] [--rationale TEXT] [--abandon]`
- accept tickets only from `human-gate`
- without --abandon: record `defer` decision, transition to `blocked` with
  human-readable blocked_reason
- with --abandon: record `reject` decision, transition to `done`
- set orchestrator status to `idle`

**Non-goals**: No automatic blocked-ticket recovery.

**Acceptance criteria**:
- defer without abandon transitions to blocked
- defer with abandon transitions to done
- correct decision type is recorded (defer vs reject)
- blocked_reason is set on defer
- tickets not in human-gate are rejected
- orchestrator state is idle

**Dependencies**: T08, T09.

---

### T24: `capsaicin ticket unblock`

**Goal**: Return a blocked ticket to `ready` for another attempt.

**Scope**:
- `capsaicin ticket unblock TICKET_ID [--reset-cycles]`
- accept tickets only from `blocked`
- record `unblock` decision
- clear `blocked_reason`
- transition to `ready`
- optionally reset cycle and retry counters
- set orchestrator status to `idle`
- append an unblock event to `activity.log`

**Non-goals**: No automatic re-run. No unblock from non-blocked states.

**Acceptance criteria**:
- blocked ticket transitions to ready
- decision row is recorded with `decision = 'unblock'`
- `blocked_reason` is cleared
- `--reset-cycles` resets cycle and retry counters
- without `--reset-cycles`, counters are preserved
- tickets not in blocked are rejected
- orchestrator state is idle

**Dependencies**: T08, T09.

---

### T25: `capsaicin status`

**Goal**: Render project and ticket status to stdout.

**Scope**:
- `capsaicin status [--ticket TICKET_ID] [--verbose]`
- without --ticket: project summary — ticket counts by status, active ticket,
  tickets in human-gate with gate_reason, blocked tickets with blocked_reason,
  next runnable ready ticket
- with --ticket: ticket detail — title, status, status_changed_at, cycle info,
  criteria with statuses, open findings grouped by severity, last run summary
- with --verbose: add run history and transition history
- all output to stdout, formatted for terminal readability

**Non-goals**: No rendered file output. No color/formatting beyond basic
alignment.

**Acceptance criteria**:
- project summary shows correct counts for each status
- active ticket is displayed when one exists
- human-gate tickets show gate_reason
- blocked tickets show blocked_reason
- next runnable ticket is identified correctly
- ticket detail shows all specified fields
- verbose mode includes run and transition history

**Dependencies**: T06, T08, T09.

---

### T26: `capsaicin resume`

**Goal**: Recover from interrupted execution based on orchestrator state.

**Scope**:
- `capsaicin resume`
- read orchestrator_state
- if idle: behave like `ticket run` (auto-select next ready ticket)
- if running with finished run: execute the post-run pipeline for the run's
  role (implementation post-run or review post-run)
- if running with unfinished run (no finished_at): treat as interrupted,
  convert to retry-or-block decision
- if awaiting_human: render human-gate context and stop
- if suspended: use resume_context to continue

**Non-goals**: No new state machine transitions beyond what T08 already
provides.

**Acceptance criteria**:
- idle state triggers ticket selection and run
- finished-but-unprocessed implementation run completes the post-run pipeline
- finished-but-unprocessed review run completes the post-run pipeline
- interrupted run (running, no finished_at) increments retry or blocks
- awaiting_human renders context without taking action
- resume after crash with completed run does not duplicate work

**Dependencies**: T15, T20.

---

### T27: `capsaicin loop`

**Goal**: Automated implement-review-revise loop.

**Scope**:
- `capsaicin loop [TICKET_ID] [--max-cycles N]`
- execute ticket run and ticket review pipelines in-process (not subprocess)
- loop: run → review → (if fail) revise → run → review → ...
- stop at human-gate (any gate_reason)
- stop at blocked (any blocked_reason)
- never auto-approve
- respect --max-cycles override or config default
- persist same orchestrator state updates as individual commands

**Non-goals**: No multi-ticket progression. No background daemon.

**Acceptance criteria**:
- loop runs implementation and review in sequence
- loop stops at human-gate and prints gate context
- loop stops at blocked and prints blocked context
- loop respects cycle limit
- loop handles adapter failures via retry/block path
- all DB state matches what individual ticket run + ticket review would produce

**Dependencies**: T15, T20.

---

## Open Decisions

These should be resolved before or during implementation of the affected
tickets.

### 1. Rendered file output (affects T06, T21, T25)

Several commands mention rendering human-readable files (ticket briefs, PR
preparation summaries). The MVP can defer file rendering and output to stdout
only. Rendered files in `renders/` can be a follow-up after the loop works.

### 2. Post-MVP completion state (affects post-MVP)

`state-machine.md` keeps `pr-ready -> done` for future automation, but MVP
stops at `pr-ready` so the human can review, create the PR, and merge
manually. A future `capsaicin pr create` or `capsaicin ticket complete`
command can own that transition.

### 3. Activity log format details (affects T05 and runtime tickets)

MVP includes `activity.log` as an append-only debug trace. The remaining choice
is just the exact line format. Recommended default: one line per event with
timestamp, event type, project_id, ticket_id, run_id, and a short JSON payload.
