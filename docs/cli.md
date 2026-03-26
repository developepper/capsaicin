# CLI

## Scope

The command surface is intentionally narrow and pragmatic.

Goals:

- initialize a local `capsaicin` project
- capture project-level config
- select one ticket for implementation
- run the implementation and review loop end to end
- persist findings and decisions locally
- stop automatically for human input when escalation rules are triggered
- render current ticket status and loop state for inspection

Non-goals:

- complex dashboards
- hosted synchronization
- broad plugin infrastructure
- deep GitHub automation on day one
- full remote issue/PR automation on day one

Command set:

1. `capsaicin init` plus SQLite schema and config
2. `capsaicin plan *` to create, review, approve, and materialize planning
   epics
3. `capsaicin ticket run` to invoke an implementer adapter and persist the run
4. `capsaicin ticket review` to invoke a reviewer adapter in a fresh session
5. bounded revise and re-review loop support in both loops
6. `capsaicin status` and `capsaicin plan status` to render current workflow
   state

Intended role split once multi-backend adapter selection exists:

- implementation loop: `Claude Code` implementer, `Codex` reviewer
- planning loop: `Codex` planner, `Claude Code` reviewer

Current implementation note:

- the shipped command layer still instantiates the Claude adapter for both
  adapter roles; planning draft runs reuse the implementer config and planning
  review runs reuse the reviewer config

## Command Contract

### `capsaicin init`

Usage:

```text
capsaicin init [--project NAME] [--repo PATH]
```

Behavior:

- create the local `.capsaicin/projects/<project-slug>/` structure
- create `capsaicin.db` and run migrations
- enable SQLite foreign-key enforcement on every connection
- write default `config.toml`
- resolve `--repo` to an absolute path before storing it
- insert the initial `projects` row
- create `activity.log` as an append-only debug trace file
- create `renders/` and `exports/` directories

### `capsaicin ticket add`

Usage:

```text
capsaicin ticket add --title TITLE --description DESC [--criteria "criterion"]
capsaicin ticket add --from FILE
```

Behavior:

- create a manual ticket for direct implementation work
- insert the ticket in `ready`
- insert acceptance criteria in `pending`
- print a human-readable ticket brief to stdout (rendered file output to
  `renders/` is optional)

File import format:

```toml
title = "Implement user authentication"
description = """
Add JWT-based authentication middleware.
"""

[[criteria]]
description = "Login endpoint returns a valid JWT"

[[criteria]]
description = "Middleware rejects expired tokens"
```

### `capsaicin ticket dep`

Usage:

```text
capsaicin ticket dep TICKET_ID --on DEPENDENCY_ID
```

Behavior:

- validate both tickets exist
- reject cycles before writing the dependency edge
- insert the dependency if valid

### `capsaicin plan new`

Usage:

```text
capsaicin plan new --problem "PROBLEM STATEMENT"
```

Behavior:

- create a `planned_epics` row in `new`
- persist the problem statement as the planning brief
- print the created epic ID and current status

### `capsaicin plan draft`

Usage:

```text
capsaicin plan draft [EPIC_ID]
```

Behavior:

- select an epic in `new` or `revise`
- transition it into `drafting`
- act as a manual stepping command rather than invoking the planner pipeline
- leave planner execution to `capsaicin plan loop`

### `capsaicin plan review`

Usage:

```text
capsaicin plan review [EPIC_ID]
```

Behavior:

- select an epic in `drafting`
- transition it into `in-review`
- act as a manual stepping command rather than invoking the planning reviewer
  pipeline
- leave planning review execution to `capsaicin plan loop`

### `capsaicin plan revise`

Usage:

```text
capsaicin plan revise [EPIC_ID] [--add-finding DESCRIPTION]
```

Behavior:

- accept epics only from `human-gate`
- optionally persist human-supplied findings
- record the human decision
- move the epic back to `revise`

### `capsaicin plan approve`

Usage:

```text
capsaicin plan approve [EPIC_ID] [--rationale TEXT] [--force]
```

Behavior:

- accept epics only from `human-gate`
- record a human approval decision
- move the epic to `approved`
- materialize implementation tickets and ticket docs under `docs/tickets/`
- make the approved epic ready for UI-driven continuation into the
  implementation loop via the materialized ticket queue
- respect `--force` when existing materialized docs were edited manually

### `capsaicin plan materialize`

Usage:

```text
capsaicin plan materialize EPIC_ID [--force]
```

Behavior:

- accept epics only from `approved`
- write or refresh the materialized implementation ticket docs
- create or update implementation-loop tickets linked back to the plan

### `capsaicin plan defer`

Usage:

```text
capsaicin plan defer [EPIC_ID] [--rationale TEXT]
```

Behavior:

- accept epics only from `human-gate`
- record a human defer decision
- move the epic to `blocked`

### `capsaicin plan unblock`

Usage:

```text
capsaicin plan unblock EPIC_ID [--reason TEXT]
```

Behavior:

- accept epics only from `blocked`
- record a human unblock decision
- move the epic back to `new`

### `capsaicin plan status`

Usage:

```text
capsaicin plan status [EPIC_ID] [--verbose]
```

Behavior without `EPIC_ID`:

- show planning totals by epic status
- show epics waiting in `human-gate`
- show active and blocked epics

Behavior with `EPIC_ID`:

- show the planning brief, status, cycle counters, and materialization state
- show planned tickets and dependencies
- show materialized implementation tickets with readiness/dependency state when
  the epic is approved
- show open planning findings
- with `--verbose`, include run and transition history

### `capsaicin plan loop`

Usage:

```text
capsaicin plan loop [EPIC_ID] [--max-cycles N]
```

Behavior:

- run the planning draft-review-revise loop automatically
- persist the same state transitions as `plan draft` and `plan review`
- stop at `human-gate`
- stop at `blocked`
- never auto-approve

### `capsaicin ticket run`

Usage:

```text
capsaicin ticket run [TICKET_ID]
```

Behavior:

- if no `TICKET_ID` is provided, pick the next `ready` ticket whose
  dependencies are all `done`, ordered by `created_at`
- when workspace isolation is enabled, acquire or validate an isolated worktree
  before proceeding; if workspace acquisition fails, the ticket moves to
  `blocked` with a `workspace_`-prefixed reason
- if the ticket is in `revise` and `current_cycle >= max_cycles`, do not invoke
  the implementer; move directly to `human-gate` with
  `gate_reason = 'cycle_limit'`
- move the ticket to `implementing`
- update `orchestrator_state` with the active ticket and run
- if starting from `ready`, set `current_cycle = 1` and reset implementation
  and review attempt counters
- if starting from `revise`, increment `current_cycle` and reset
  `current_impl_attempt = 1`
- assemble an implementer `RunRequest`
- prompt assembly inputs should include:
  - ticket title and description
  - acceptance criteria with current statuses
  - prior open findings when revising
  - cycle number and max cycles
  - explicit implementer role instruction
  - explicit scope constraint
- invoke the implementer adapter
- insert the run with `exit_status = 'running'` before invocation, then update
  it to the terminal status after invocation
- persist the run record and the resulting diff
- if the run succeeds with a non-empty diff, move to `in-review`
- if the run succeeds with an empty diff, move to `human-gate` with
  `gate_reason = 'empty_implementation'`
- if the run is blocked by permission denials, move to `human-gate` with
  `gate_reason = 'permission_denied'`; do not consume retries
- when transitioning to `human-gate`, set `orchestrator_state.status =
  'awaiting_human'`
- if the run fails or times out, increment implementation retry state and
  either retry or move to `blocked`

### `capsaicin ticket review`

Usage:

```text
capsaicin ticket review [TICKET_ID] [--allow-drift]
```

Behavior:

- find the ticket in `in-review`
- when workspace isolation is enabled, validate the isolated worktree before
  proceeding; if the workspace is missing or stale, the ticket moves to
  `blocked` with a `workspace_`-prefixed reason
- verify that the current `git diff HEAD` (tracked files only) matches the
  `run_diffs.diff_text` captured at the end of the implementation run; if
  they differ, reject the review with a workspace-drift error unless
  `--allow-drift` is provided; when `--allow-drift` is used, re-capture the
  current diff as the new `run_diffs` baseline and proceed with review against
  the updated diff
- capture and persist the tracked-file review baseline before invoking the
  reviewer
- update `orchestrator_state` with the active review run
- assemble a reviewer `RunRequest` with `diff_context`
- reviewer prompt assembly inputs should include:
  - explicit independent reviewer role instruction
  - the captured diff being reviewed
  - ticket title, description, and acceptance criteria
  - prior findings with dispositions
  - explicit JSON schema-constrained output instruction
  - anti-bias instruction not to trust commit messages or inline rationale
- invoke the reviewer adapter in `read-only` mode
- insert the run with `exit_status = 'running'` before invocation, then update
  it to the terminal status after invocation
- capture the post-review diff and compare it to baseline
- if the reviewer modified tracked files, mark the run
  `contract_violation`, discard findings, and retry or block
- validate the parsed reviewer result
- if parsing or validation fails, mark the run `parse_error` and retry or block
- on valid `fail`, persist findings, update acceptance-criteria statuses for
  checked criteria, and move to `revise`
- on valid `pass` with `confidence: high` or `confidence: medium`, move to
  `human-gate` with `gate_reason = 'review_passed'`
- on valid `pass` with `confidence: low`, move to `human-gate` with
  `gate_reason = 'low_confidence_pass'`
- on valid `escalate`, move to `human-gate` with
  `gate_reason = 'reviewer_escalated'`
- if the cycle limit is hit, prefer `human-gate` with
  `gate_reason = 'cycle_limit'`
- when transitioning to `human-gate`, set `orchestrator_state.status =
  'awaiting_human'`

Acceptance-criteria update rule:

- match reviewer `criteria_checked` entries to `acceptance_criteria` rows by
  `criterion_id`
- match reviewer findings to criteria by `acceptance_criterion_id`
- if a checked criterion has a blocking finding with a matching
  `acceptance_criterion_id`, mark it `unmet`
- if a checked criterion has no blocking finding with a matching
  `acceptance_criterion_id`, mark it `met`
- if a criterion was not checked in this review, leave its status unchanged
- findings with `acceptance_criterion_id = null` are general findings not tied
  to a specific criterion

Finding reconciliation on re-review cycles:

- on `verdict: pass`, mark all prior open findings for the ticket as `fixed`
  with `resolved_in_run` pointing to the preceding implementation run
- on `verdict: fail`, match incoming findings to prior open findings by
  `(category, location, description_prefix)` fingerprint:
  - matched: update the prior finding's description and severity, link to the
    new review run
  - unmatched prior: mark as `fixed`
  - unmatched new: persist as new open findings

Review source-of-truth note:

- the diff basis is tracked files only via `git diff HEAD`
- the reviewer reviews the diff captured at the end of the implementation run,
  not the current working tree state
- the pre-review workspace drift check ensures the working tree still matches
  the captured diff; if it does not, review is rejected unless `--allow-drift`
  is provided
- manual edits made after `ticket run` invalidate the original review baseline;
  `--allow-drift` establishes a new baseline by re-capturing the current diff,
  so the review covers the actual workspace state rather than stale content

### `capsaicin ticket approve`

Usage:

```text
capsaicin ticket approve [TICKET_ID] [--rationale TEXT] [--force]
```

Behavior:

- find the ticket in `human-gate`
- verify that the current `git diff HEAD` matches the diff that was reviewed;
  if they differ, reject approval unless `--force` is provided, because the
  workspace no longer matches what was reviewed
- record a human approval decision
- if `gate_reason` is `cycle_limit`, `reviewer_escalated`, or
  `low_confidence_pass`, require a rationale because approval is overriding
  an unresolved quality gate
- move the ticket to `pr-ready`
- set `orchestrator_state.status = 'idle'`
- print a PR preparation summary to stdout

Current behavior:

- `pr-ready` is a terminal human-handoff state
- PR creation and merge remain manual

### `capsaicin ticket revise`

Usage:

```text
capsaicin ticket revise [TICKET_ID] [--add-finding DESCRIPTION] [--reset-cycles]
```

Behavior:

- find the ticket in `human-gate`
- optionally add human-supplied findings
- record the decision
- move the ticket to `revise`
- optionally reset cycle and retry counters
- set `orchestrator_state.status = 'idle'`

### `capsaicin ticket complete`

Usage:

```text
capsaicin ticket complete [TICKET_ID] [--rationale TEXT]
```

Behavior:

- accept tickets only from `pr-ready`
- record a human completion decision
- move the ticket to `done`
- unblock dependent tickets whose prerequisites are now satisfied

### `capsaicin ticket defer`

Usage:

```text
capsaicin ticket defer [TICKET_ID] [--rationale TEXT] [--abandon]
```

Behavior:

- record a human defer decision
- accept tickets only from `human-gate`
- if `--abandon` is provided, record decision `reject` and move the ticket to
  `done`
- otherwise move the ticket to `blocked` and set a human-readable
  `blocked_reason`
- set `orchestrator_state.status = 'idle'`

### `capsaicin ticket unblock`

Usage:

```text
capsaicin ticket unblock TICKET_ID [--reset-cycles]
```

Behavior:

- accept tickets only from `blocked`
- record a human unblock decision
- move the ticket to `ready`
- clear `blocked_reason`
- optionally reset cycle and retry counters
- set `orchestrator_state.status = 'idle'`

### `capsaicin status`

Usage:

```text
capsaicin status [--ticket TICKET_ID] [--verbose]
```

Behavior without `--ticket`:

- show project totals by ticket status
- show the active ticket from `orchestrator_state`
- show tickets waiting in `human-gate` with `gate_reason`
- show blocked tickets with `blocked_reason`
- show the next runnable `ready` ticket

Behavior with `--ticket`:

- show title, status, `status_changed_at`, and cycle information
- show acceptance criteria and their statuses
- show open findings grouped by severity
- show the last run summary
- with `--verbose`, include run history and transition history

### `capsaicin resume`

Usage:

```text
capsaicin resume
```

Behavior:

- read `orchestrator_state`
- if `idle`, behave like `ticket run`
- if `running`, inspect the active run and determine whether to reprocess,
  retry, or block
- if the active run already finished, execute the same post-run pipeline that
  `ticket run` or `ticket review` would have executed based on the run role
- if the active run is still marked `running` with no `finished_at`, treat it
  as interrupted and convert it into a retry-or-block decision
- if `awaiting_human`, render the human-gate context and stop
- if `suspended`, use `resume_context` to continue from the interrupted step

### `capsaicin loop`

Usage:

```text
capsaicin loop [TICKET_ID] [--max-cycles N]
```

Behavior:

- run the implement-review-revise loop automatically
- persist the same `orchestrator_state` updates as the individual commands it
  subsumes
- execute the `ticket run` and `ticket review` pipelines in-process rather than
  shelling out to CLI subprocesses
- stop at `human-gate`
- stop at `blocked`
- never auto-approve

This should be the primary hands-off command, with `ticket run` and
`ticket review` serving as manual step controls.

Loop and resume have different entry paths:

- `loop` advances work by ticket state
- `resume` recovers work by `orchestrator_state`

### `capsaicin ui`

Usage:

```text
capsaicin ui [--port PORT] [--no-open] [--repo PATH] [--project SLUG]
```

Behavior:

- resolve the project context the same way other commands do
- start a local HTTP server bound to `127.0.0.1` only
- pick an available port automatically unless `--port` is provided
- open the default browser unless `--no-open` is passed
- close the project-resolution database connection before starting the server;
  the web layer opens its own per-request connections
- serve a Starlette ASGI application with Jinja2 templates and HTMX

The UI exposes these operator surfaces:

- project dashboard with orchestrator state, inbox, queue, blocked tickets,
  next runnable ticket, and recent activity
- planning dashboard with epic queues, approved epic detail, planned tickets,
  materialized implementation tickets, and planning findings
- ticket detail with metadata, acceptance criteria, open findings, diagnostic
  messages, last run details, implementation diff, run history, and transition
  history
- approved-epic continuity actions: re-materialize docs, inspect implementation
  ticket readiness, and continue implementation on the next eligible ticket or
  a selected ticket within the epic
- human-gate action forms: approve (with rationale and force options), revise
  (with optional finding and cycle reset), defer (with rationale and abandon),
  and unblock (with optional cycle reset)
- workflow trigger buttons: run implementation, run review (with allow-drift),
  run loop, and resume
- server-sent events for narrow live updates of dashboard sections and ticket
  detail when state changes

All actions delegate to the same shared command services used by the CLI.
The UI does not reinterpret state-machine rules or add new workflow logic.

Runtime model:

- no authentication, remote access, or multi-user support
- per-request SQLite connections opened and closed within each request
- WAL mode is not enabled by default
- SSE endpoints poll the database on a short interval rather than using
  filesystem or database notifications
- the server runs until the operator presses Ctrl+C

### `capsaicin doctor`

Usage:

```text
capsaicin doctor [--repo PATH] [--project SLUG]
```

Behavior:

- run preflight checks against the resolved repo and project config
- verify the configured adapter command is available on `PATH`
- verify the repo path exists and is a git worktree
- warn on a dirty working tree
- verify local Claude permission settings required for write-capable runs
- when workspace isolation is enabled, check worktree support, the resolved
  worktree root directory, and writable git metadata
- exit non-zero when required checks fail

### `capsaicin workspace`

Manage isolated git worktrees used for agent execution.  These commands are
only meaningful when `[workspace] enabled = true` in the project config.

#### `capsaicin workspace status`

Usage:

```text
capsaicin workspace status TICKET_ID [--repo PATH] [--project SLUG]
```

Behavior:

- report the isolation mode for the ticket: `shared` (isolation disabled),
  `worktree` (active worktree exists), `branch` (branch exists but worktree
  removed), or `none` (no workspace record)
- show workspace ID, status, branch name, worktree path, and failure reason
  when applicable
- surface any blocking condition that would prevent the next pipeline run

#### `capsaicin workspace recover`

Usage:

```text
capsaicin workspace recover TICKET_ID [--repo PATH] [--project SLUG]
```

Behavior:

- clean up a failed or stale workspace and create a fresh one
- reuses an existing healthy workspace if validation passes
- requires workspace isolation to be enabled; errors otherwise

#### `capsaicin workspace cleanup`

Usage:

```text
capsaicin workspace cleanup TICKET_ID [--repo PATH] [--project SLUG]
```

Behavior:

- tear down the worktree and optionally delete the branch (controlled by
  `auto_cleanup` config)
- refuses to clean up a dirty worktree with uncommitted changes; surfaces a
  `cleanup_conflict` failure for manual resolution
- no-op when no workspace exists or it is already cleaned
