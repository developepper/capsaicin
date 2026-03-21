# Architecture

## Local-First Design

This project is intentionally local-first.

GitHub matters for:

- issue creation after planning is approved
- pull requests after a ticket is ready
- final human review before merge

But the workflow itself should be able to start and run locally without
depending on remote APIs.

`init` should resolve the repo path to an absolute path before storing it so
later commands can run from subdirectories without ambiguity.

## State Model

`capsaicin` should use a local database as its primary system of record for
workflow execution.

Why:

- the workflow is fundamentally stateful
- the current pain is manual orchestration and state tracking
- findings, loops, escalations, and resumability fit structured data better
  than loose documents
- a database can feed agents the right context at the right time

The likely default is `SQLite`.

The database should manage:

- project state
- epic and ticket records
- dependency relationships
- agent run history
- findings and dispositions
- decisions and human escalations
- queue state
- state transitions over time

## Human-Readable Views

Even if the database is the primary state layer, the system should remain
inspectable.

Important records should be renderable into readable text on demand:

- project summaries
- epic summaries
- ticket briefs
- review reports
- finding lists
- decision logs
- issue body drafts
- PR preparation summaries

## Storage Strategy

The right design is:

- `config.toml` for configuration (source of truth; DB snapshot refreshed per
  invocation)
- `database` for operational state
- `activity.log` for append-only human-readable debug tracing
- `rendered text views` for human inspection
- `exported issue and PR content` for GitHub

Markdown can still exist as an export or editable interface when useful, but it
should not be the only or primary workflow engine.

## Local Layout

```text
.capsaicin/
  projects/<project-slug>/
    capsaicin.db
    config.toml
    activity.log
    renders/
      project-summary.md
      epics/
      tickets/
      reviews/
    exports/
      github/
        issues/
        prs/
```

`renders/` should be treated as generated human-readable views, not as the
canonical system of record. `exports/` should be reserved for outward-facing
artifacts such as GitHub issue bodies and PR summaries. The database remains
canonical.

`activity.log` should be a lightweight append-only debug trace rather than a
second system of record. It should record events such as project init, ticket
creation, state transitions, run start/finish, drift detection,
parse/contract failures, human decisions, and unblock actions. The database
remains canonical.

Recommended line format:

- one line per event
- ISO 8601 timestamp first
- event type token
- project_id, ticket_id, and run_id when available
- compact JSON payload at the end for extra detail

Rendered files under `renders/` are generated views rather than canonical
state. Operator-facing inspection can happen through stdout, generated renders,
`activity.log`, and any UI that reads from the same project state.

## Runtime And Packaging

The implementation language is not chosen finally yet, but the current
recommendation is:

- use `Python` for the current implementation
- keep the contracts and data model strict enough that a later Rust rewrite is
  possible without redesign

Python is the pragmatic first choice because subprocess orchestration, SQLite,
JSON, TOML, and rapid iteration are all straightforward there.

## Integration Philosophy

`capsaicin` should integrate with tools the developer already uses rather than
forcing a proprietary agent runtime.

Primary targets:

- `Codex`
- `Claude Code`

Initial backend priority:

1. `Claude Code` reviewer adapter
2. `Claude Code` implementer adapter
3. `Codex` implementer adapter

`Claude Code` is the strongest first reviewer target because structured output,
session isolation, and read-only execution are easier to enforce there.

`Codex` is still a good implementation target, but its current output model is
much weaker for machine-consumable review findings.

## Application Layer

The `app/` package provides shared service and read-model boundaries that
both the CLI and future web UI consume:

```text
src/capsaicin/app/
  context.py       # AppContext — shared project/config/DB resolution
  commands/        # workflow mutations returning structured CommandResult
  queries/         # read models returning dataclasses (no rendering)
```

Import direction:

- `cli.py` → `app.context`, `app.commands`, `app.queries`
- `web/*` → `app.context`, `app.commands`, `app.queries`
- `app.commands/*` → existing workflow modules (`ticket_run`, etc.)
- `app.queries/*` → DB helpers and `ticket_status` query functions

Existing workflow modules remain the implementation engines. Command
services are thin orchestration wrappers that return structured outcomes.
Query services return serializable dataclasses that delivery layers
format for their output channel.

## Boundaries

To avoid endless looping, `capsaicin` should use bounded retry rules.

Suggested defaults:

- up to 3 planning review/fix cycles before mandatory human review
- up to 3 implementation review/fix cycles before mandatory human review
- any blocked state requires a human decision before progression

The system should optimize for good outcomes, not infinite polishing.
