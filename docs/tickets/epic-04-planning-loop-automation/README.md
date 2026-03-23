# Epic 04: Planning Loop Automation

## Goal

Add a first-class planning loop to `capsaicin` so work can start from a
problem statement, iterate through planner and reviewer passes, stop at a human
approval gate, and then materialize approved implementation tickets with all
required context for the existing implementation loop.

## Why This Epic Exists

`capsaicin` already automates the implementation loop once a good ticket
exists. The next gap is the work that happens before manual ticket entry:

- shaping a problem into an epic and scoped ticket set
- reviewing the plan independently before implementation starts
- iterating on plan findings instead of hand-editing docs ad hoc
- producing implementation tickets that are self-contained for a fresh coding
  session

The target workflow is:

1. human provides a problem statement
2. planner drafts an epic and ticket plan in structured local state
3. reviewer reviews the planning artifacts
4. planner revises until there are no blocking findings or the loop escalates
5. human approves the plan
6. the approved plan is rendered into implementation tickets that the existing
   `capsaicin` ticket loop can consume

## Scope

This epic includes:

- planning-domain entities, migrations, and state transitions
- planner and planning-reviewer run contracts
- CLI and shared app services for the planning loop
- bounded planning draft/review/revise orchestration
- human approval and status inspection for planning records
- materialization of approved plans into implementation-ticket docs with
  explicit references and implementation notes
- local UI support for planning status and actions

This epic does not include:

- GitHub issue creation or `gh` API automation
- PR automation
- multi-epic portfolio orchestration
- automatic decomposition of one active implementation ticket into many live
  implementation runs

## Deliverable Shape

The planning loop should introduce local planning records that are canonical in
the database and renderable into human-readable artifacts. An approved plan
must be able to generate implementation-ticket documents that contain:

- clear goal, scope, non-goals, acceptance criteria, and dependencies
- `References` listing the docs and prior ticket material that must be read
- `Implementation Notes` containing constraints, helper guidance, fixtures,
  validation expectations, and any context needed for a fresh session

Those generated ticket docs become the handoff boundary into the existing
implementation loop.

## Sequencing

The intended implementation order is:

1. [T01](./T01.md) planning data model and state-machine foundation
2. [T02](./T02.md) planner and planning-reviewer contracts
3. [T03](./T03.md) planning command surface and read models
4. [T04](./T04.md) planning loop orchestration and resume behavior
5. [T05](./T05.md) approval flow and implementation-ticket materialization
6. [T06](./T06.md) planning UI surfaces
7. [T07](./T07.md) explicit implementation completion from `pr-ready` to `done`

## Open Design Constraints

- Planning should reuse the local-first architecture and app/query boundaries
  already established for the implementation loop.
- Planner and reviewer runs must remain independently invokable fresh sessions.
- Human approval remains mandatory before plan materialization.
- Generated implementation tickets must be deterministic enough to edit by hand
  later without losing the canonical planning lineage.
- The implementation loop should not be rewritten to depend on planning-loop
  state for basic operation; manual ticket creation should remain possible.

## References

- [docs/overview.md](../../overview.md)
- [docs/architecture.md](../../architecture.md)
- [docs/state-machine.md](../../state-machine.md)
- [docs/data-model.md](../../data-model.md)
- [docs/adapters.md](../../adapters.md)
- [docs/configuration.md](../../configuration.md)
- [docs/cli.md](../../cli.md)
- [docs/tickets/README.md](../README.md)
- [docs/tickets/roadmap.md](../roadmap.md)
- [src/capsaicin/db.py](../../../src/capsaicin/db.py)
- [src/capsaicin/state_machine.py](../../../src/capsaicin/state_machine.py)
- [src/capsaicin/orchestrator.py](../../../src/capsaicin/orchestrator.py)
- [src/capsaicin/prompts.py](../../../src/capsaicin/prompts.py)
- [src/capsaicin/loop.py](../../../src/capsaicin/loop.py)
- [src/capsaicin/app/context.py](../../../src/capsaicin/app/context.py)
- [src/capsaicin/app/commands/loop.py](../../../src/capsaicin/app/commands/loop.py)
- [src/capsaicin/migrations/0001_initial_schema.sql](../../../src/capsaicin/migrations/0001_initial_schema.sql)
