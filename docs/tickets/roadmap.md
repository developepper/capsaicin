# Roadmap

This document is the single place for future work that is not yet planned into
an epic.

Use it for:

- sequencing decisions
- future directions
- product ideas
- nice-to-have improvements

When a direction becomes concrete enough to scope, move it into a dedicated
epic directory under `docs/tickets/`. Once an epic is complete, move it to
`docs/tickets/archive/`.

This file should answer two questions:

- what should be worked on next
- what ideas should be kept in view but are not yet planned

## Product Direction

`capsaicin` should evolve into a local workflow engine for supervised software
delivery.

That means the project is not just “an AI coding tool” or a thin wrapper around
whatever model is currently strongest. The core value is the workflow layer:

- explicit state instead of implicit chat history
- bounded loops instead of endless agent churn
- independent review and verification instead of self-certification
- human gates at meaningful decisions
- artifact lineage, resumability, and inspectability across the whole flow

The intended shape of the product is:

- planning upstream
- implementation in the middle
- verification and review as the backbone
- GitHub and PR handoff downstream

The strategic goal is to make AI-assisted delivery operationally trustworthy,
not just individually impressive in one-off agent runs.

## Evolution

The expected evolution of the product is:

1. implementation loop foundation
2. reliability, diagnostics, and local UI
3. planning-loop automation
4. adapter diversification and role-specialized agent pairings
5. GitHub issue and PR handoff
6. stronger verification, isolation, lineage, and policy controls
7. later, multi-ticket orchestration and higher-level operational tooling

If the project evolves well, it should remain centered on:

- state machines
- run contracts
- persistence
- review policy
- human decisions
- reproducible artifacts

Adapters and models can change over time. The workflow engine is the product.

## Priority Order

The current intended order for the next major work streams is:

1. ~~reliability and diagnostics for the implementation loop~~ (done — Epic 02)
2. ~~a UI for the existing implementation loop~~ (done — Epic 03)
3. planning-loop automation (scoped as
   [epic-04-planning-loop-automation](./epic-04-planning-loop-automation/))
4. GitHub handoff and PR automation

Multi-ticket orchestration remains later work.

## Reliability And Diagnostics

Focus on making the existing loop easier to trust, diagnose, and operate.

This work is now scoped as
[epic-02-reliability-and-diagnostics](archive/epic-02-reliability-and-diagnostics/).

Candidate areas:

- detect and surface Claude Code `permission_denials`
- distinguish permission-blocked runs from true empty implementations
- surface agent result text when implementation produces no diff
- improve human-gate diagnostics and `status --verbose` output
- add a `capsaicin doctor` or `capsaicin check` command
- validate local adapter setup and repository preconditions
- log run cost and other high-signal adapter metadata in `activity.log`
- improve automatic ticket selection in `loop`, including `revise` tickets

## UI For The Existing Implementation Loop

A local operator UI is now implemented as part of
[epic-03-ui-for-implementation-loop](archive/epic-03-ui-for-implementation-loop/).

The UI launches from `capsaicin ui`, runs a built-in HTTP server on
`127.0.0.1`, and ships inside the Python package with no separate app install
or build step.

What was delivered:

- shared service and query boundaries between CLI and web (`app/commands/`,
  `app/queries/`)
- Starlette ASGI runtime with Jinja2 templates and vendored HTMX
- project dashboard with orchestrator state, inbox, queue, blocked tickets,
  next runnable ticket, and recent activity
- ticket detail with acceptance criteria, findings, diagnostics, diff, run
  history, and transition history
- human-gate action forms for approve, revise, defer, and unblock
- workflow triggers for run, review, loop, and resume
- SSE live updates for dashboard and ticket detail
- `python-multipart` dependency for form parsing

What is intentionally not included:

- ticket-creation UI
- auth, remote access, or multi-user support
- SPA or frontend build pipeline
- GitHub integration UI

## Planning Loop

Extend the product upstream from manual ticket entry to structured planning and
review.

This work is now scoped as
[epic-04-planning-loop-automation](./epic-04-planning-loop-automation/).

Key areas:

- problem statement intake
- planner and reviewer runs for plan creation
- epic and ticket records for planning state
- human approval before issue export
- issue-body generation from approved plans

## GitHub Handoff And PR Automation

Automate the work that currently begins at `pr-ready`.

Candidate areas:

- export PR summaries and issue bodies
- branch and commit preparation
- `gh` integration for PR creation
- explicit completion flow from `pr-ready` to `done`

## Codex Adapter Support

Broaden adapter support beyond the current Claude Code-only runtime wiring.

Current direction:

- support the intended implementation-loop pairing: `Claude Code` implementer
  and `Codex` reviewer
- support the intended planning-loop pairing: `Codex` planner and `Claude Code`
  planning reviewer
- replace direct `ClaudeCodeAdapter` instantiation in command services with
  backend-driven adapter selection from config

Candidate areas:

- implement a `CodexAdapter` against the existing `BaseAdapter` contract
- add adapter-factory selection based on `[adapters.<role>].backend`
- normalize Codex-specific run metadata and failure modes into the existing
  `RunResult` shape
- add tests for backend selection and Codex reviewer execution
- evaluate whether Codex planner support needs contract extensions for
  planning-loop use

## Workflow Policy And Capability Modeling

Make agent/runtime capabilities and workflow policy explicit instead of
hard-coding them into adapter assumptions.

Candidate areas:

- model adapter capabilities such as structured output, read-only enforcement,
  permission-denial reporting, and cost metadata
- allow workflow policy to depend on capabilities rather than backend names
- support policy profiles for different repo types or risk levels
- make reviewer, verifier, and human-gate requirements more configurable

## Prompt Versioning And Reproducibility

Treat prompt evolution and run reproducibility as first-class operational
concerns.

Candidate areas:

- version prompt templates and persist prompt-version identifiers per run
- store prompt fingerprints and important config fingerprints with run records
- make it easy to answer which prompt/config/model combination produced a
  result
- support project-level prompt overrides without losing reproducibility

## Artifact Lineage And Auditability

Strengthen the system's ability to explain how planning and implementation
artifacts were produced.

Candidate areas:

- track lineage from problem statement to plan to implementation ticket to
  review and approval outcomes
- persist richer causal links between runs, findings, decisions, and exports
- make human decisions and overrides easy to inspect later
- improve “why did the orchestrator do this?” introspection

## Verification And Quality Gates

Extend the loop from AI review alone to explicit verification policies.

Candidate areas:

- model linting, tests, type checks, and project-specific validation as
  first-class workflow steps or gate inputs
- let projects declare required verification before a ticket can reach
  `human-gate` or `pr-ready`
- distinguish reviewer judgment from mechanical verification evidence
- surface verification failures clearly in CLI/UI status and history views

## Workspace Isolation And Execution Safety

Reduce the risk of workflow interference from unrelated local changes and
prepare the system for stronger automation.

Candidate areas:

- branch or worktree management per ticket
- disposable or isolated execution environments for agent runs
- better drift handling and workspace cleanliness checks before execution
- safer recovery paths when local state and repo state diverge

## Import, Export, And Handoff Boundaries

Make it easier to move work into and out of `capsaicin` without losing
structure or lineage.

Candidate areas:

- ingest planning input from docs, issue templates, or existing ticket sources
- export richer issue bodies, PR summaries, and status reports
- preserve traceability when work is materialized, exported, or re-imported
- keep local canonical state while improving interoperability with external
  systems

## Observability, Analytics, And Cost Controls

Improve trust, diagnosability, and operational feedback as usage scales.

Candidate areas:

- richer run timelines and failure taxonomy
- aggregated cost and usage reporting by project, ticket, and loop stage
- review-quality and retry-loop analytics
- operator-facing diagnostics explaining stuck or inefficient workflows
- budget policies and spend-aware workflow controls

## Later Directions

Ideas that remain intentionally later:

- adapter diversification beyond current backends
- smarter ticket decomposition
- multi-ticket orchestration with dependency-aware parallel work
