# capsaicin

`capsaicin` is a local-first autonomous ticket loop for AI-assisted software
development.

It is designed for developers who want more than "run an agent on a task." The
goal is to support a continuous workflow where planning, implementation, review,
revision, and human feedback happen in a controlled loop until a ticket is
actually ready to move forward.

The name fits the Developepper theme and includes `ai` in the middle:
`capsaicin`.

## What Problem It Solves

Many current AI coding workflows automate parts of planning or implementation,
but they usually stop short of the most important quality control step:
independent review with feedback fed back into the loop before moving on.

That creates a predictable failure mode:

- implementation is generated
- review is manual, inconsistent, or skipped
- missed acceptance criteria survive longer than they should
- the human has to manage the entire state machine by hand
- progress across tickets becomes tedious and fragile

`capsaicin` is meant to solve the orchestration problem, not just the coding
problem.

## Product Vision

`capsaicin` should be a reusable open-source project that can work across
different repositories and different developers with similar goals.

It should:

- work locally on a developer machine
- support `Codex`, `Claude Code`, or both
- avoid requiring OpenAI or Anthropic API integration
- use structured local state for planning and execution
- support one-ticket-at-a-time implementation with independent review
- stop for human feedback only when needed
- keep persistent local state so work can resume cleanly
- keep workflow state human-inspectable and exportable
- produce output that is ready for GitHub issues and pull requests

It should not:

- replace human judgment
- force a hosted service
- require GitHub to manage early planning
- let the same execution session self-certify completion
- advance to the next ticket without a real quality gate

Open architectural questions still to be resolved:

- exact agent invocation and communication model
- implementation language and packaging model
- how strict session isolation is enforced across tools

## Core Workflow

`capsaicin` manages two major loops:

1. planning loop
2. implementation loop

Each loop has explicit review and human-gate steps.

### Planning Loop

The planning loop starts from a problem statement and ends with an approved
local plan that is ready to seed into GitHub issues.

Flow:

1. Human describes the problem or desired outcome.
2. Planner agent drafts an epic and a set of digestible tickets in structured
   local state.
3. Reviewer agent reviews the planning records.
4. If findings exist, the planner revises the plan.
5. Repeat until review returns no blocking findings.
6. Human approves the plan.
7. GitHub epic and ticket issues are created from the approved local plan.

### Implementation Loop

The implementation loop starts from one approved ticket and ends only when that
ticket is PR-ready.

Flow:

1. Select one ticket whose dependencies are satisfied.
2. Implementer agent works the ticket.
3. Reviewer agent reviews the resulting changes.
4. If findings exist, the implementer fixes them.
5. Repeat until review returns no blocking findings.
6. Human performs the final gate.
7. Create or update the pull request.
8. Move to the next ticket only after the current one is actually ready.

## Why This Workflow

This workflow is based on a simple observation: separate review catches real
problems.

An independent reviewer often catches:

- missed acceptance criteria
- incomplete deliverables
- hidden regressions
- weak tests
- architecture drift
- scope expansion that should have been split into a follow-up ticket

The workflow already works well manually. The missing piece is automation around
state, handoff, persistence, and escalation.

## Actor Model

`capsaicin` should support these roles:

- `Human`: sets goals, resolves ambiguity, approves planning, approves merge
  readiness
- `Planner`: drafts and revises epic/ticket planning records
- `Implementer`: makes code and documentation changes for a ticket
- `Reviewer`: critiques planning artifacts or code changes and blocks
  advancement when needed

Recommended dual-agent mode:

- `Codex` for planning or implementation
- `Claude Code` for review

Alternative:

- swap planning roles if one tool produces stronger project decomposition

Single-agent mode should still be supported, but review must happen in a
separate fresh session. The same session should not certify its own completion.

## State Machine

The core design is a bounded state machine, not a loose chain of prompts.

### Planning States

- `planning/new`
- `planning/drafting`
- `planning/in-review`
- `planning/revise`
- `planning/human-gate`
- `planning/approved`
- `planning/blocked`

### Ticket States

- `ticket/ready`
- `ticket/implementing`
- `ticket/impl-review-ready`
- `ticket/in-review`
- `ticket/revise`
- `ticket/human-gate`
- `ticket/pr-ready`
- `ticket/blocked`
- `ticket/done`

Each state should have:

- required inputs
- a responsible actor
- a completion condition
- an escalation condition

## Human Gates

`capsaicin` should not ask the user for routine continuation. It should ask
only when a real decision or blocker exists.

Examples:

- multiple valid scope cuts exist
- a product or architecture tradeoff is required
- acceptance criteria appear incomplete or misleading
- implementation reveals hidden dependencies
- a reviewer recommends splitting scope into a follow-up ticket
- environment issues block meaningful verification

This is supervised autonomy, not blind autonomy.

## Review Policy

Review is a blocking quality gate.

The reviewer should check for:

- correctness
- regressions
- unmet acceptance criteria
- missing tests
- architecture violations
- hidden scope creep
- insufficient ticket definitions

The system should not allow "looks good" reviews to replace actual findings or
explicit no-finding outcomes.

## Local-First Design

This project is intentionally local-first.

GitHub matters for:

- issue creation after planning is approved
- pull requests after a ticket is ready
- final human review before merge

But the workflow itself should be able to start and run locally without
depending on remote APIs.

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
- review history
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

This keeps the system transparent without forcing the user to manage workflow
state manually in markdown.

## Storage Strategy

The right design is probably:

- `database` for operational state
- `rendered text views` for human inspection
- `exported issue and PR content` for GitHub

Markdown can still exist as an export or editable interface when useful, but it
should not be the only or primary workflow engine.

## Possible Local Layout

A reusable project still needs a portable local layout. A likely structure
looks like this:

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

This keeps the real state in the database while still making important outputs
easy to inspect.

`renders/` should be treated as generated human-readable views, not as the
canonical system of record. `exports/` should be reserved for outward-facing
artifacts such as GitHub issue bodies and PR summaries. The database remains
canonical.

## Core Data Model

The implementation-loop-first MVP should assume entities along these lines:

- `projects`
- `tickets`
- `acceptance_criteria`
- `ticket_dependencies`
- `agent_runs`
- `run_diffs`
- `findings`
- `decisions`
- `state_transitions`

Planning entities such as `epics` and outward-facing entities such as
`exports` can be added after the core loop is validated.

## MVP SQLite Schema

The first schema should be scoped to the implementation loop only. Planning
tables such as epics and exports can come later once the core run/review/revise
loop is validated.

Suggested MVP tables:

- `projects`
- `tickets`
- `acceptance_criteria`
- `ticket_dependencies`
- `agent_runs`
- `run_diffs`
- `findings`
- `state_transitions`
- `decisions`

Suggested shape:

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
    status      TEXT NOT NULL DEFAULT 'ready'
                CHECK (status IN (
                    'ready','implementing','in-review',
                    'revise','human-gate','pr-ready',
                    'blocked','done'
                )),
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
    role              TEXT NOT NULL CHECK (role IN ('implementer','reviewer','planner')),
    mode              TEXT NOT NULL CHECK (mode IN ('read-write','read-only')),
    exit_status       TEXT NOT NULL CHECK (exit_status IN (
                          'success','failure','timeout',
                          'contract_violation','parse_error'
                      )),
    prompt            TEXT NOT NULL,
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

CREATE TABLE findings (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES agent_runs(id),
    ticket_id    TEXT NOT NULL REFERENCES tickets(id),
    severity     TEXT NOT NULL CHECK (severity IN ('blocking','warning','info')),
    category     TEXT NOT NULL,
    location     TEXT,
    description  TEXT NOT NULL,
    disposition  TEXT NOT NULL DEFAULT 'open'
                 CHECK (disposition IN ('open','fixed','wont_fix','disputed')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
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
                    'approve','reject','revise','defer','escalate'
                )),
    rationale   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

MVP schema notes:

- use ULIDs or UUIDs for text primary keys that appear in envelopes, logs, and
  rendered reports
- keep `structured_result` and `adapter_metadata` as JSON blobs for MVP rather
  than over-normalizing early
- keep large diffs in `run_diffs` instead of embedding them directly in
  `agent_runs`
- treat reviewer runs as `agent_runs` with `role = 'reviewer'` rather than
  creating a separate `reviews` table
- omit `impl-review-ready` from persisted ticket state; it is a transition
  condition rather than a useful long-lived status

## Dependency Handling

Dependencies matter because the system should not advance a ticket whose
prerequisites are not satisfied.

The model should support:

- explicit ticket-to-ticket dependencies
- readiness checks before ticket selection
- blocked-state transitions when dependencies are unmet
- detection of invalid dependency graphs such as cycles

Circular dependencies should be treated as planning errors and escalated rather
than worked around implicitly.

## Failure Recovery

The system also needs a recovery model for interrupted or failed runs.

Examples:

- agent process crashes
- command times out
- verification fails
- a run produces partial output and stops

The orchestrator should persist enough state to support safe resume:

- run start and end status
- partial outputs when available
- last successful state transition
- whether the ticket is safe to retry automatically
- whether human intervention is required

`blocked` is not enough by itself. The implementation should distinguish
between recoverable run failure, review-blocked work, and true human-decision
blockers.

## MVP

The first useful version should be narrow and pragmatic.

MVP goals:

- initialize a local `capsaicin` project
- capture project-level config
- select one ticket for implementation
- run the implementation and review loop end to end
- persist findings and decisions locally
- stop automatically for human input when escalation rules are triggered
- render current ticket status and loop state for inspection

The MVP does not need to solve everything at once.

It does not need:

- complex dashboards
- hosted synchronization
- broad plugin infrastructure
- deep GitHub automation on day one
- full planning-loop automation on day one

The MVP may also reasonably start with the implementation loop first and add
the planning loop second. The implementation-review-fix cycle is the highest
value part of the system and the most immediate relief for the current manual
workflow pain.

Suggested MVP sequence:

1. `capsaicin init` plus SQLite schema and config
2. `capsaicin ticket run` to invoke an implementer adapter and persist the run
3. `capsaicin ticket review` to invoke a reviewer adapter in a fresh session
4. bounded revise and re-review loop support
5. `capsaicin status` to render current workflow state
6. planning-loop support and GitHub export after the core loop works

## Likely CLI Shape

The eventual CLI might look something like:

```text
capsaicin init
capsaicin plan start
capsaicin plan review
capsaicin plan approve
capsaicin issues create
capsaicin ticket next
capsaicin ticket run
capsaicin ticket review
capsaicin ticket approve
capsaicin resume
capsaicin status
```

These are not final commands, but they reflect the intended workflow.

## Agent Invocation Model

This is currently the riskiest design area and should be treated as an early
technical spike, not an afterthought.

`capsaicin` is intended to drive local agent CLIs such as `Codex` and
`Claude Code` without requiring API integrations.

Likely options:

- subprocess execution with stdin/stdout capture
- subprocess execution with prompt and result handoff through files
- a hybrid model where the orchestrator writes structured inputs to disk and
  captures a final machine-readable or text result from stdout

Current direction:

- `Codex` is likely a natural subprocess fit, but `capsaicin` should not trust
  stdout as the source of truth for what changed
- `Claude Code` offers a cleaner structured-output path for non-interactive
  execution and is the strongest initial target for reviewer runs
- the orchestrator should capture workspace change evidence itself, especially
  post-run diffs, instead of relying on the agent to describe edits faithfully

Practical implication:

- prefer `Claude Code` as the first reviewer backend
- treat `Codex` as an implementer-first backend in MVP
- avoid relying on natural-language parsing for review verdicts when a
  structured-output path exists

Practical caveats for `Claude Code` reviewer runs:

- `--output-format json` wraps the interaction, so the adapter still needs to
  extract and validate the reviewer's inner JSON result from assistant text
- `--max-turns` should be set intentionally to bound review exploration and
  cost
- review cost is real and should eventually be governed by config defaults such
  as max turns or other budget controls

The design requirements are:

- deterministic invocation from the orchestrator
- capture of agent output for persistence and review
- explicit context assembly per run
- no hidden dependence on long-lived chat state
- support for fresh reviewer sessions

The exact adapter contract is not decided yet and should be validated early.

## Adapter Contract

Adapters should stay thin. Their job is to translate between `capsaicin`'s run
contract and a specific CLI tool.

The orchestrator should provide at least:

- working directory path
- role assignment such as `implementer`, `reviewer`, or `planner`
- assembled task prompt
- diff context when the role is reviewing an existing change set
- context file paths or explicit context payloads
- timeout budget
- constraints such as review scope or read-only expectations

One useful normalization is an explicit execution mode:

- `read-write` for implementers
- `read-only` for reviewers

The adapter should return at least:

- exit status such as success, failure, or timeout
- structured result when the CLI supports it
- raw stdout and stderr as fallback evidence
- duration and run metadata

For reviewer runs, the adapter contract should additionally require a
machine-consumable verdict payload at minimum:

- `verdict`: `pass`, `fail`, or `escalate`
- `confidence`: `high`, `medium`, or `low`
- `findings`: list of structured findings with severity, category,
  description, and optional location
- `scope_reviewed`: structured evidence of what the reviewer actually checked

A practical review result shape is:

```json
{
  "verdict": "pass | fail | escalate",
  "confidence": "high | medium | low",
  "findings": [
    {
      "id": "string",
      "severity": "blocking | warning | info",
      "category": "string",
      "location": "string | null",
      "description": "string",
      "disposition": "open | fixed | wont_fix | disputed"
    }
  ],
  "scope_reviewed": {
    "files_examined": ["string"],
    "tests_run": true,
    "criteria_checked": ["string"]
  }
}
```

Interpretation rules:

- `verdict: pass` may still include `warning` or `info` findings
- `verdict: fail` must include at least one `blocking` finding
- `verdict: escalate` means the reviewer could not complete a reliable review
  without human input
- `confidence: low` should generally force a human gate even if the verdict is
  `pass`

Finding IDs should be assigned by the orchestrator when findings are persisted.
Reviewer-emitted IDs, if any, should not be treated as canonical.

When prior findings are fed back into later runs, their dispositions should be
explicit so the loop can distinguish newly open work from items the implementer
claims to have fixed or intentionally disputed.

Validation rules for reviewer `structured_result` should be explicit:

- `verdict: fail` requires at least one `blocking` finding
- `verdict: pass` cannot include any `blocking` findings
- `confidence: high` is invalid if `files_examined` is empty
- `confidence: high` is invalid if acceptance criteria were provided but
  `criteria_checked` is empty
- top-level review result fields must always be present, even when empty or
  false

If validation fails, the adapter should return `exit_status: parse_error` and
preserve the raw output for debugging rather than trying to repair the result
silently.

The orchestrator, not the adapter, should additionally capture:

- post-run git diff or equivalent workspace change evidence
- state transitions
- persistence into the local database
- next-step decisions

Adapters should not decide workflow progression or own cross-ticket state.

## Run Envelope

`capsaicin` should define a formal request and result envelope between the
orchestrator and adapters.

Run request envelope:

```json
{
  "run_id": "string",
  "role": "implementer | reviewer | planner",
  "mode": "read-write | read-only",
  "working_directory": "string",
  "prompt": "string",
  "diff_context": "string | null",
  "context_files": ["string"],
  "acceptance_criteria": [
    {
      "id": "string",
      "description": "string",
      "status": "pending | met | unmet | disputed"
    }
  ],
  "prior_findings": [],
  "timeout_seconds": 0,
  "max_turns": 0,
  "adapter_config": {}
}
```

Run result envelope:

```json
{
  "run_id": "string",
  "exit_status": "success | failure | timeout | contract_violation | parse_error",
  "duration_seconds": 0,
  "raw_stdout": "string",
  "raw_stderr": "string",
  "structured_result": {},
  "adapter_metadata": {}
}
```

Notes:

- `structured_result` is usually `null` for implementer runs and populated for
  reviewer runs
- `diff_context` is usually `null` for implementer runs and populated for
  reviewer runs from orchestrator-captured diff state
- `prior_findings` lets revise and re-review loops operate without hidden
  session history
- `acceptance_criteria` should remain first-class data rather than being buried
  only inside the prompt
- `adapter_metadata` is for tool-specific details such as model, turn count, or
  cost that do not drive workflow state directly

## Fresh Session Requirement

Independent review is load-bearing. `capsaicin` should define "fresh session"
concretely rather than treating it as a vague prompt instruction.

At minimum, a fresh review session should mean:

- a new process invocation
- no inherited interactive conversation history
- context supplied only from the orchestrator-selected inputs
- explicit role assignment as reviewer
- persisted review output linked to a unique run record

If a reviewer run is marked `read-only`, the orchestrator should verify that no
unexpected file changes occurred. A non-empty post-review diff should be
treated as an adapter or enforcement failure.

The validation should be baseline-based:

1. capture tracked-file diff state before the reviewer starts
2. capture tracked-file diff state after the reviewer exits
3. compare the two snapshots
4. if the diff changed, mark the run invalid as a contract violation

For MVP, this check only needs to cover tracked-file diffs. Untracked build
artifacts can be handled later if they become a practical problem.

Reviewer prompts should also explicitly warn against treating commit messages,
inline rationale, or self-justifying artifacts as evidence that the
implementation is correct. The review should be grounded in the ticket,
acceptance criteria, and actual diff.

If a given CLI tool cannot provide meaningful session isolation, that weakness
needs to be documented in the adapter and possibly compensated for by stricter
input packaging or process boundaries.

## Runtime And Packaging

The implementation language is not chosen yet, but it should be treated as an
early project decision because it affects packaging, contributor experience,
and local reliability.

The two obvious candidates are:

- `Python`: faster iteration, easier subprocess orchestration, broad user
  familiarity
- `Rust`: stronger single-binary distribution, stronger type safety, better fit
  for long-lived CLI tooling

Current recommendation:

- use `Python` for the adapter-validation spike and MVP
- keep the contracts and data model strict enough that a later Rust rewrite is
  possible without redesign

This decision should still be made intentionally rather than deferred
indefinitely, but the current evidence points to Python first.

## Configuration

`capsaicin` will need explicit project and runtime configuration.

Likely configuration areas:

- agent selection by role
- adapter command paths
- default repo or workspace paths
- retry limits
- escalation rules
- review policy knobs
- render and export preferences
- GitHub integration settings

The `config.toml` shown in the local layout is intended to hold this class of
configuration, but the schema is still to be defined.

## Integration Philosophy

`capsaicin` should integrate with tools the developer already uses rather than
forcing a proprietary agent runtime.

Primary targets:

- `Codex`
- `Claude Code`

Support should focus on:

- role assignment per phase
- prompt/context assembly
- invocation of local CLI tools
- capture of outputs and findings into structured state plus human-readable
  renders

Initial backend priority:

1. `Claude Code` reviewer adapter
2. `Claude Code` implementer adapter
3. `Codex` implementer adapter

`Claude Code` is the strongest first reviewer target because structured output,
session isolation, and read-only execution are easier to enforce there.

`Codex` is still a good implementation target, but its current output model is
much weaker for machine-consumable review findings.

## Design Principles

- local-first over hosted-first
- explicit state over implicit chat history
- one ticket at a time
- independent review before advancement
- human gate on ambiguity and final acceptance
- structured local state with human-readable views
- repo-agnostic workflow
- bounded loops, not endless agent churn

## Boundaries And Stop Conditions

To avoid endless looping, `capsaicin` should use bounded retry rules.

Suggested defaults:

- up to 3 planning review/fix cycles before mandatory human review
- up to 3 implementation review/fix cycles before mandatory human review
- any blocked state requires a human decision before progression

The system should optimize for good outcomes, not infinite polishing.

## Inspiration

Projects like `chief` and `gstack` are useful reference points for orchestration
ideas and role separation, but `capsaicin` is aimed at a more explicit
review-feedback loop and a structured local planning process that happens
before GitHub issue creation.

## Current Direction

The project definition at this stage is:

`capsaicin` is an open-source local orchestrator for AI-assisted project
planning and ticket execution that uses review-feedback loops, structured local
state, and human gates to move work from problem statement to PR-ready
completion.

## Next Planning Steps

The next artifacts to define are:

1. the README-level product scope and constraints
2. the agent adapter and invocation contract
3. the persistent local data model and schema
4. human-readable render and export formats
5. the CLI command model
6. the MVP implementation plan

Immediate planning priority:

- validate the adapter contract against real local `Codex` and `Claude Code`
  invocations before investing heavily in the full schema
