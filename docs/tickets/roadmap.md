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

## Priority Order

The current intended order for the next major work streams is:

1. reliability and diagnostics for the implementation loop
2. a UI for the existing implementation loop
3. planning-loop automation
4. GitHub handoff and PR automation

Multi-ticket orchestration remains later work.

## Reliability And Diagnostics

Focus on making the existing loop easier to trust, diagnose, and operate.

This work is now scoped as
[epic-02-reliability-and-diagnostics](./epic-02-reliability-and-diagnostics/).

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

Preferred delivery model:

- launch from `capsaicin ui`
- start a built-in local web server bound to `127.0.0.1`
- choose an available port automatically unless `--port` is provided
- open the browser automatically by default, with a `--no-open` option
- ship inside the Python package with no separate app install, Node build step,
  or Docker requirement

Preferred architecture:

- Python web layer in the existing repo and package
- server-rendered UI over the existing SQLite/config state
- shared application services between CLI and UI rather than subprocess-driven
  UI actions
- HTMX plus Jinja2-style templating as the default frontend approach
- Starlette or FastAPI as the HTTP layer, with a bias toward the smallest
  framework that cleanly supports routing, templates, static files, and SSE
- SSE for narrowly scoped live updates such as active runs, activity feed
  updates, and human-gate arrival

Initial UI priorities:

1. dashboard with project overview, queue state, and active work
2. ticket detail with acceptance criteria, findings, run history, and diff
3. human-gate screen with clear approve, revise, and defer actions
4. activity feed and run diagnostics, including cost, permission denials, and
   short agent result text

What to avoid initially:

- no separate frontend build pipeline
- no React or SPA-first architecture unless later requirements force it
- no ticket-creation UI
- no auth or multi-user assumptions
- no shelling out to CLI commands from the web layer when shared Python
  services can be called directly

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
