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
4. planning-to-implementation continuity
5. adapter diversification and role-specialized agent pairings
6. GitHub issue and PR handoff
7. stronger verification, isolation, lineage, and policy controls
8. later, multi-ticket orchestration and higher-level operational tooling

If the project evolves well, it should remain centered on:

- state machines
- run contracts
- persistence
- review policy
- human decisions
- reproducible artifacts

Adapters and models can change over time. The workflow engine is the product.

Completed foundations:

- implementation loop foundation
- reliability and diagnostics:
  [epic-02-reliability-and-diagnostics](archive/epic-02-reliability-and-diagnostics/)
- local operator UI:
  [epic-03-ui-for-implementation-loop](archive/epic-03-ui-for-implementation-loop/)
- planning-loop automation:
  [epic-04-planning-loop-automation](archive/epic-04-planning-loop-automation/)
- planning-to-implementation continuity

## Short-Term Priorities

This section is the near-term sequencing view. `Evolution` above is the
long-term arc; this section is just the next set of work streams to keep in
focus.

1. GitHub handoff and PR automation
2. workflow policy and capability modeling to support cleaner backend
   diversification
3. adapter diversification and role pairings

Multi-ticket orchestration remains later work.

## Planning Loop Follow-Ons

The base planning loop is now shipped. The remaining work is around smoother
handoff, stronger exports, and operational polish around the planning surfaces.

Key areas:

- better issue-body and export generation from approved plans
- richer operator views around plan history and materialization state
- stronger auditability and policy controls around planning decisions

## GitHub Handoff And PR Automation

Automate the work that currently begins at `pr-ready`.

Candidate areas:

- export PR summaries and issue bodies
- branch and commit preparation
- `gh` integration for PR creation
- explicit completion flow from `pr-ready` to `done`

## Workflow Policy And Capability Modeling

Make agent/runtime capabilities and workflow policy explicit instead of
hard-coding them into adapter assumptions.

This becomes more important before or alongside adapter diversification.

Candidate areas:

- model adapter capabilities such as structured output, read-only enforcement,
  permission-denial reporting, and cost metadata
- allow workflow policy to depend on capabilities rather than backend names
- support policy profiles for different repo types or risk levels
- make reviewer, verifier, and human-gate requirements more configurable

## Adapter Diversification And Role Pairings

Broaden adapter support beyond the current Claude Code-only runtime wiring.

Current direction:

- validate the adapter abstraction with at least one strong non-Claude backend
- preserve role-specialized pairings rather than assuming one backend should do
  everything
- likely target pairings today are:
  - implementation loop: `Claude Code` implementer and `Codex` reviewer
  - planning loop: `Codex` planner and `Claude Code` planning reviewer
- replace direct `ClaudeCodeAdapter` instantiation in command services with
  backend-driven adapter selection from config

Candidate areas:

- implement a second adapter backend against the existing `BaseAdapter`
  contract, with Codex as the current likely target
- add adapter-factory selection based on `[adapters.<role>].backend`
- normalize non-Claude-specific run metadata and failure modes into the
  existing `RunResult` shape
- add tests for backend selection and role-specific execution paths
- evaluate whether planning roles need contract extensions beyond the current
  implementation-loop assumptions

## Prompt Versioning And Reproducibility

Treat prompt evolution and run reproducibility as first-class operational
concerns.

This becomes more important once planning and multi-backend execution are both
real.

Candidate areas:

- version prompt templates and persist prompt-version identifiers per run
- store prompt fingerprints and important config fingerprints with run records
- make it easy to answer which prompt/config/model combination produced a
  result
- support project-level prompt overrides without losing reproducibility

## Artifact Lineage And Auditability

Strengthen the system's ability to explain how planning and implementation
artifacts were produced.

This becomes more important once planning artifacts, exports, and GitHub
handoff all exist.

Candidate areas:

- track lineage from problem statement to plan to implementation ticket to
  review and approval outcomes
- persist richer causal links between runs, findings, decisions, and exports
- make human decisions and overrides easy to inspect later
- improve “why did the orchestrator do this?” introspection

## Verification And Quality Gates

Extend the loop from AI review alone to explicit verification policies.

This becomes more important once the implementation loop and GitHub handoff are
stable enough to support stricter delivery policy.

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

This becomes more important once automation expands beyond a single local loop
and into handoff or parallel work.

Candidate areas:

- branch or worktree management per ticket
- disposable or isolated execution environments for agent runs
- better drift handling and workspace cleanliness checks before execution
- safer recovery paths when local state and repo state diverge

## Import, Export, And Handoff Boundaries

Make it easier to move work into and out of `capsaicin` without losing
structure or lineage.

This becomes more important once planning export and GitHub handoff are active
surfaces.

Candidate areas:

- ingest planning input from docs, issue templates, or existing ticket sources
- export richer issue bodies, PR summaries, and status reports
- preserve traceability when work is materialized, exported, or re-imported
- keep local canonical state while improving interoperability with external
  systems

## Observability, Analytics, And Cost Controls

Improve trust, diagnosability, and operational feedback as usage scales.

This becomes more important as loop usage, model diversity, and operational
cost all increase.

Candidate areas:

- richer run timelines and failure taxonomy
- aggregated cost and usage reporting by project, ticket, and loop stage
- review-quality and retry-loop analytics
- operator-facing diagnostics explaining stuck or inefficient workflows
- budget policies and spend-aware workflow controls

## Post-Materialization Plan Evolution

Handle the case where implementation work reveals that an already materialized
plan needs to change.

This remains intentionally later because it introduces hard questions about
lineage, partial progress, and regeneration safety.

Candidate areas:

- revising a plan after some generated implementation tickets already exist
- reconciling changed plans with in-progress or completed implementation work
- preserving auditability when plan structure changes mid-execution
- guiding operators through safe regeneration, follow-up tickets, or manual
  divergence

## Later Directions

Ideas that remain intentionally later:

- adapters beyond Codex and Claude Code
- smarter ticket decomposition
- multi-ticket orchestration with dependency-aware parallel work
