# Backlog

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

## Priority Order

The current intended order for the next major work streams is:

1. reliability and diagnostics for the implementation loop
2. a UI for the existing implementation loop
3. planning-loop automation
4. GitHub handoff and PR automation

Multi-ticket orchestration remains later work.

## Reliability And Diagnostics

Focus on making the existing loop easier to trust, diagnose, and operate.

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

Add a local operator UI on top of the existing database and filesystem state.

The initial UI should focus on the current implementation loop rather than
introducing a different workflow model.

Candidate areas:

- project overview and ticket queue
- active ticket and run state
- human-gate inbox
- findings, acceptance criteria, and decision history
- run history, costs, and diagnostics
- actions for run, review, approve, revise, defer, unblock, loop, and resume

## Planning Loop

Extend the product upstream from manual ticket entry to structured planning and
review.

Candidate areas:

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

## Later Directions

Ideas that remain intentionally later:

- cost controls and budgeting
- workflow analytics and audit reporting
- adapter diversification beyond current backends
- smarter ticket decomposition
- multi-ticket orchestration with dependency-aware parallel work
