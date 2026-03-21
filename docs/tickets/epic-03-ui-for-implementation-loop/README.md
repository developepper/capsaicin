# Epic 03: UI For The Implementation Loop

**Status**: Planned

Add a local operator UI for the existing implementation loop without changing
the workflow model or coupling the product to a specific frontend stack.

The main design constraint for this epic is replaceability. The first UI can
be server-rendered HTML with HTMX, but the durable architecture should sit one
level lower:

- domain rules and state machine remain independent of presentation
- application services own workflow actions
- read models own operator-facing queries
- CLI and web UI are delivery layers over shared services

This keeps the initial UI pragmatic while preserving a future path to a
different web frontend, JSON API surface, or TUI without rewriting core
workflow logic.

## Product Direction

The initial UI should:

- launch from `capsaicin ui`
- run a built-in local HTTP server bound to `127.0.0.1`
- choose an open port automatically unless `--port` is provided
- open the browser automatically by default, with `--no-open` support
- ship in the Python package with no separate app install, Node toolchain, or
  Docker requirement

The initial UI should not:

- introduce a new workflow beyond the current implementation loop
- shell out to CLI commands when shared Python services can be invoked directly
- require a SPA or frontend build pipeline
- assume auth, remote access, or multi-user coordination
- add ticket-creation UI in the first pass

## Architecture Decisions

This epic adopts these working decisions:

- use a Python web layer in the existing package
- prefer Starlette as the initial HTTP layer unless later requirements justify
  something heavier
- use Jinja2 templates for server rendering
- use HTMX for incremental updates and form actions
- use SSE only for narrow live-update surfaces such as run progress, activity
  feed updates, and human-gate arrival
- extract structured service and query boundaries before leaning on templates
  as the primary composition layer

The UI stack is an implementation choice, not the architectural center. The
core boundary is shared services plus read models.

Additional working decisions:

- use an explicit package split for shared command services and read models once
  T01 begins; the current module count justifies more structure than a single
  flat helper file
- keep HTMX responses HTML-first; do not introduce a general JSON API in this
  epic unless a specific route cannot be served cleanly as HTML or SSE
- for long-running UI-triggered actions, prefer redirecting to the relevant
  ticket or dashboard surface and showing progress there rather than holding
  the request open

Recommended package shape for the T01 extraction:

```text
src/capsaicin/
  app/
    __init__.py
    context.py          # shared project/config/db resolution for CLI and web
    commands/
      __init__.py
      run_ticket.py
      review_ticket.py
      approve_ticket.py
      revise_ticket.py
      defer_ticket.py
      unblock_ticket.py
      resume.py
      loop.py
    queries/
      __init__.py
      dashboard.py
      ticket_detail.py
      inbox.py
      activity.py
      diagnostics.py
  web/
    __init__.py
    app.py
    routes/
    templates/
    static/
```

Mapping guidance:

- existing `ticket_*.py`, `resume.py`, and `loop.py` modules remain the
  workflow engines initially
- `app.commands.*` should be thin orchestration entry points over those modules
- `ticket_status.py` and `diagnostics.py` should be mined into
  `app.queries.*` read models rather than expanded further as CLI-only modules
- `cli.py` and the future web layer should depend on `app.context`,
  `app.commands`, and `app.queries`, not directly on the lower-level workflow
  modules except where transitional glue is unavoidable

## Session Start Guidance

Each ticket in this epic should be implementable in a fresh session.

Before coding a ticket:

1. read this epic README
2. read the target ticket
3. read every file listed in that ticket's `References`
4. treat the ticket's `Implementation Notes` as binding scope guidance

Baseline docs for the whole epic:

- `README.md`
- `docs/README.md`
- `docs/overview.md`
- `docs/architecture.md`
- `docs/state-machine.md`
- `docs/data-model.md`
- `docs/configuration.md`
- `docs/cli.md`
- `docs/tickets/README.md`
- `docs/tickets/roadmap.md`

General implementation standards for this epic:

- keep CLI behavior intact unless a ticket explicitly changes shared service
  boundaries
- do not duplicate orchestration logic in web handlers
- prefer structured view models over HTML-specific helper functions
- keep the UI local-first and deterministic in tests
- prefer integration tests against ASGI routes and service functions over
  browser automation unless a ticket explicitly needs full-stack coverage
- add dependencies sparingly and only when they improve the long-term boundary
  rather than just accelerating a single screen
- when a ticket touches a shared workflow action, read the corresponding action
  module and its tests before changing code

## Execution Strategy

The first work should not be templates. The critical path is extracting stable
service and query seams so the UI is a thin delivery layer rather than a second
implementation of the product.

Key sequencing decisions:

- T01 must land before substantial UI work because it establishes the shared
  action and read-model boundaries the web layer depends on.
- T01 is intentionally broad but should be implemented in two internal slices:
  command services first, then read models. It remains one planning ticket
  because both slices define a single shared boundary.
- T02 depends on T01 because the HTTP runtime and route layer should be built
  on those new boundaries rather than directly on CLI functions.
- T03 and T04 can proceed in parallel once T02 exists.
- T05 depends on T02, T03, and T04 because live updates should attach to real
  dashboard and ticket-detail surfaces rather than land as abstract plumbing.
- T06 depends on T03, T04, and T05 because it ties the main flows together into
  a coherent operator path.
- T07 is last because packaging, launch ergonomics, and docs should reflect the
  actual implemented shape.

## Phased Roadmap

| Phase | Focus | Tickets |
|-------|-------|---------|
| 1 | Shared Boundaries | T01 |
| 2 | Web Runtime | T02 |
| 3 | Read Models And Screens | T03-T04 |
| 4 | Live Updates | T05 |
| 5 | Human-Gate Flow | T06 |
| 6 | Packaging And Docs | T07 |

## Suggested PR / Milestone Grouping

| Milestone | Tickets | PR Strategy |
|-----------|---------|-------------|
| M1: Service And Query Boundary | T01 | Own PR |
| M2: Web Shell | T02 | Own PR |
| M3: Main Screens | T03, T04 | Single PR or two small PRs |
| M4: Live Status | T05 | Own PR |
| M5: Human-Gate UX | T06 | Own PR |
| M6: Launch And Documentation | T07 | Own PR |

## Risks

1. **UI code may accidentally become the new orchestration layer.** If route
   handlers call low-level modules ad hoc, the web UI will fork behavior from
   the CLI. T01 exists specifically to prevent this.

2. **Template-first delivery can hide missing contracts.** Rendering strings in
   templates is fast, but it makes later UI replacement expensive. Shared
   structured read models need to own screen data.

3. **Long-running actions can degrade the request model.** Implementation and
   review runs are not normal CRUD actions. The UI must surface run state
   cleanly without pretending every action is instantaneous.

4. **Concurrent CLI and UI access can stress SQLite if connection handling is
   naive.** The web layer should keep transactions short and should explicitly
   evaluate whether WAL mode is needed for the local single-operator model.

5. **SSE can sprawl if it is used too broadly.** Keep it narrow and additive.
   Polling or normal page refresh remains acceptable for many views.

6. **A web UI can invite accidental multi-user assumptions.** This epic is
   still single-operator and local-first. Do not smuggle in auth, tenancy, or
   remote deployment concerns.

7. **Packaging weight can grow quickly.** New dependencies should justify
   themselves against the project's local, simple-install goals.

## Open Questions To Resolve During Implementation

- whether SQLite WAL mode should be enabled by default for mixed CLI/UI local
  usage, or documented as a runtime decision in the web layer work

Resolved direction:

- use an explicit package split rather than a single top-level `services.py`
- keep the initial web layer HTML-first except for `text/event-stream`
- redirect long-running actions to ticket or dashboard views with live status

Explicit initial UI scope note:

- the first main UI surfaces are dashboard, ticket detail, live status, and
  human-gate actions
- UI triggers for `run`, `review`, `resume`, and `loop` belong in the epic and
  should be introduced through the dashboard/ticket flows once the route,
  progress, and live-status patterns are in place
