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

## Later Directions

Ideas that remain intentionally later:

- cost controls and budgeting
- workflow analytics and audit reporting
- adapter diversification beyond current backends
- smarter ticket decomposition
- multi-ticket orchestration with dependency-aware parallel work
