# Tickets

This directory contains planning materials, roadmap notes, and archived ticket sets organized by epic.

Use this directory for work that changes over time:

- active epic plans and ticket breakdowns
- roadmap and future-direction notes
- archived completed epics that should not be required reading for project context

## Ticket Authoring Convention

Active implementation tickets should be written so they can be picked up in a fresh coding session without relying on prior chat context.

Each active ticket should include:

- clear goal, scope, non-goals, acceptance criteria, and dependencies
- `References`: the docs and archived tickets that must be read first
- `Implementation Notes`: constraints, shared-helper guidance, fixture usage, test expectations, and other coding context that should not be rediscovered ad hoc

The intended session-start workflow is:

1. read the active epic `README.md`
2. read the specific ticket file
3. read every path listed under that ticket's `References`
4. implement using the constraints in that ticket's `Implementation Notes`

## Structure

- [roadmap.md](./roadmap.md): future work, sequencing, and open directions that are not yet scoped into an epic
- [archive/](./archive/): completed epic plans retained for history

## Archive

| Epic                         | Directory                                                                             |
|------------------------------|---------------------------------------------------------------------------------------|
| Epic 01: Implementation Loop | [archive/epic-01-implementation-loop-mvp](./archive/epic-01-implementation-loop-mvp/) |
| Epic 02: Reliability And Diagnostics | [epic-02-reliability-and-diagnostics](archive/epic-02-reliability-and-diagnostics/) |
| Epic 03: UI For Implementation Loop | [epic-03-ui-for-implementation-loop](archive/epic-03-ui-for-implementation-loop/) |
