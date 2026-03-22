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
- full planning-loop automation on day one

Command set:

1. `capsaicin init` plus SQLite schema and config
2. `capsaicin ticket run` to invoke an implementer adapter and persist the run
3. `capsaicin ticket review` to invoke a reviewer adapter in a fresh session
4. bounded revise and re-review loop support
5. `capsaicin status` to render current workflow state
6. planning-loop support and GitHub export as separate work streams

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

- create a manual ticket because planning-loop automation is handled separately
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

### `capsaicin ticket run`

Usage:

```text
capsaicin ticket run [TICKET_ID]
```

Behavior:

- if no `TICKET_ID` is provided, pick the next `ready` ticket whose
  dependencies are all `done`, ordered by `created_at`
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
- ticket detail with metadata, acceptance criteria, open findings, diagnostic
  messages, last run details, implementation diff, run history, and transition
  history
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
