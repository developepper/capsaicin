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

Completed foundations:

- implementation loop foundation
- reliability and diagnostics:
  [epic-02-reliability-and-diagnostics](archive/epic-02-reliability-and-diagnostics/)
- local operator UI:
  [epic-03-ui-for-implementation-loop](archive/epic-03-ui-for-implementation-loop/)
- planning-loop automation:
  [epic-04-planning-loop-automation](archive/epic-04-planning-loop-automation/)
- planning-to-implementation continuity
- adapter diversification and role-specialized agent pairings:
  [adapter-diversification-with-evidence-driven-backend-validation](archive/adapter-diversification-with-evidence-driven-backend-validation/)

## Short-Term Priorities

This section is the near-term sequencing view. `Evolution` above is the
long-term arc; this section is just the next set of work streams to keep in
focus.

1. GitHub handoff and PR automation
2. operator experience and workflow polish on top of the shipped planning,
   implementation, and workspace flows
3. workflow policy and capability modeling to support cleaner multi-backend
   execution
4. stronger verification, audit, and policy controls around planning and
   implementation runs

Multi-ticket orchestration remains later work.

## Planning Loop Follow-Ons

The base planning loop is now shipped. The remaining work is around smoother
handoff and operational polish around the planning surfaces.

Key areas:

- smoother plan authoring and revision flows, including stronger operator
  support for drafting and editing planning briefs
- better issue-body and export generation from approved plans
- richer operator views around plan history and materialization state
- stronger auditability and policy controls around planning decisions

## Operator Experience And Workflow Surfaces

The workflow engine is ahead of the operator experience. A focused round of UX
work should make the existing model easier to run confidently without reducing
the strictness of review and human gates.

Candidate areas:

- tighter UI controls for start, pause, resume, and explicit handoff actions
- richer live views for logs, diffs, findings, and recent transitions
- clearer queue navigation across epics, materialized tickets, and blocked work
- guided onboarding such as `capsaicin new` or `capsaicin quickstart` that can
  walk an operator from problem statement through initial planning and loop
  setup
- better visibility into branch, commit, and workspace state while a loop is
  running

## Workspace Follow-Ons And Handoff Integration

Workspace isolation and recovery are now part of the shipped local workflow.
The remaining work is not "add worktrees" but "make isolated execution pay off
everywhere downstream."

Candidate areas:

- configurable bootstrap/setup commands for isolated workspaces so project
  dependencies and local tooling can be prepared automatically
- workspace-aware commit and PR preparation that consumes the approval-time
  metadata already being recorded
- cleaner automatic cleanup hooks tied to completion, defer/abandon, and later
  merge flows
- stronger operator guidance for stuck setup or teardown states, including
  better surfaced recovery recommendations
- optional stronger sandboxing beyond git worktrees, such as disposable
  environments for higher-risk execution
- more efficient workspace read models for dashboards and ticket detail views
  so the UI does not rely on repeated per-ticket status resolution

## GitHub Handoff And PR Automation

Automate the work that currently begins at `pr-ready`.

Workspace isolation is now in place, so the handoff work can assume
branch/worktree-aware execution state instead of the old shared-worktree model.

Candidate areas:

- export PR summaries and issue bodies
- branch and commit preparation
- automatic commit creation after successful implementation runs, with commit
  messages derived from ticket metadata and workflow context
- branch-aware handoff that carries ticket, epic, and workspace context
  through to PR preparation
- `gh` integration for PR creation
- merge, cleanup, and completion flows after human approval
- explicit completion flow from `pr-ready` to `done`

## Workflow Policy And Capability Modeling

Make agent/runtime capabilities and workflow policy explicit instead of
hard-coding them into adapter assumptions.

This becomes more important now that multi-backend execution exists.

Candidate areas:

- model adapter capabilities such as structured output, read-only enforcement,
  permission-denial reporting, and cost metadata
- allow workflow policy to depend on capabilities rather than backend names
- support policy profiles for different repo types or risk levels
- make reviewer, verifier, and human-gate requirements more configurable

## Multi-Backend Follow-Ons

The adapter diversification foundation is now in place. Remaining work is about
operational polish, broader backend support, and clearer backend capability
modeling rather than first-time multi-backend enablement.

Candidate areas:

- add more adapter backends beyond the current Claude Code and Codex support
- keep backend capability differences explicit in policy rather than hidden in
  adapter-specific code paths
- refine role-specific defaults and recommended pairings as backend behavior is
  validated in real usage
- extend backend validation and audit surfaces where operators still need more
  inspectability

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

## Generated Continuity Artifacts

Generate compact, human-readable continuity artifacts from canonical state
without making those artifacts the source of truth.

This should serve both planning and implementation flows while giving operators
and agents a stable summary of the current situation.

Candidate areas:

- generate progress-oriented summaries of approved scope, open findings, recent
  decisions, and current execution status
- make continuity artifacts available for operator inspection, prompt assembly,
  and handoff surfaces without duplicating workflow authority
- define refresh rules so generated summaries stay aligned with database state
- keep the mechanism shared across planning, implementation, and export
  surfaces so the artifacts do not drift by audience

## Distribution And Installation

Reduce adoption friction by making the tool easier to install, upgrade, and
validate on a fresh machine.

Candidate areas:

- package and publish installable releases through a stable distribution path
- provide a one-command install or bootstrap flow for common local setups
- add environment and dependency checks that can validate agent CLI and git
  readiness early
- document and automate upgrade paths for both stable users and contributors

## Observability, Analytics, And Cost Controls

Improve trust, diagnosability, and operational feedback as usage scales.

This becomes more important as loop usage, model diversity, and operational
cost all increase.

Candidate areas:

- richer run timelines and failure taxonomy
- aggregated cost and usage reporting by project, ticket, and loop stage
- review-quality and retry-loop analytics
- operator-facing diagnostics explaining stuck or inefficient workflows
- timeline views that connect runs, diffs, commits, and decisions in one place
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
