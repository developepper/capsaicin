# Epic 01: Implementation Loop MVP

Build the end-to-end implementation loop: init a project, add tickets, run
implementer, run reviewer, handle human gates, and drive the bounded
implement-review-revise cycle to completion.

## Execution Strategy

The MVP builds the implementation loop bottom-up in 9 phases. Foundation
(schema, config, DB) comes first, then project/ticket CRUD, then the state
machine and orchestrator internals, then the adapter layer, then the two
pipelines (implement, review), then the human-gate commands, then
status/recovery, and finally the automated loop that wires everything together.

Key sequencing decisions:

- T08 and T09 can proceed in parallel (both depend on T03, not on each other).
- T10 is independent of T08/T09 and can start as soon as T01 is done.
- T12 can proceed independently of T13. T13 depends on T17 (review result
  validation) so T17 must land before or alongside T13.
- T17, T18, T19 are independent review-support modules that can all be built in
  parallel after T10/T03.
- T21, T22, T23, T24 are independent human-gate commands that can all be built
  in parallel.
- T27 is a pure integration ticket — it calls T15 and T20 pipelines in-process.
- CI should be phased in with the implementation work rather than as a separate
  epic:
  - T01: initial GitHub Actions workflow with docs/meta checks, Python setup,
    editable install, `ruff format --check`, `capsaicin --help`, and `pytest`
  - T05: extend CI with `capsaicin init` smoke coverage
  - T12/T13: rely on captured fixtures and mocked subprocess tests in CI; do
    not add live Claude integration to CI

No merges or splits from the T01-T27 structure in `plan.md`. Each ticket has a
clear boundary.

## Phased Roadmap

| Phase | Focus | Tickets | Status |
|-------|-------|---------|--------|
| 1 | Foundation | T01-T04 | Complete |
| 2 | Project & Ticket Management | T05-T07 | Complete |
| 3 | State Machine & Orchestrator | T08-T09 | Not started |
| 4 | Adapter Layer | T10-T13 | Not started |
| 5 | Implementation Pipeline | T14-T15 | Not started |
| 6 | Review Pipeline | T16-T20 | Not started |
| 7 | Human Gate & Decisions | T21-T23 | Not started |
| 8 | Status & Recovery | T24-T26 | Not started |
| 9 | Automated Loop | T27 | Not started |

## Suggested PR / Milestone Grouping

| Milestone | Tickets | PR Strategy |
|-----------|---------|-------------|
| M1: Foundation | T01, T02, T03, T04 | 1 PR each (small, sequential) or single PR for T01-T04 |
| M2: Project & Tickets | T05, T06, T07 | 1 PR each; T05 is the integration point |
| M3: State Machine & Orchestrator | T08, T09 | 1 PR each (parallel development possible) |
| M4: Adapter Types & Prompts | T10, T11 | Single PR (tightly coupled) |
| M5: Implementer Adapter | T12 | Own PR (implementer mode only) |
| M6: Review Validation | T17 | Own PR (must land before T13) |
| M7: Reviewer Adapter | T13 | Own PR (imports T17 validator; can follow M6 immediately) |
| M8: Diff & Baseline | T14, T16 | Single PR (T16 extends T14) |
| M9: Review Support | T18, T19 | Single PR (independent, both feed T20) |
| M10: Implementation Pipeline | T15 | Own PR (first full pipeline) |
| M11: Review Pipeline | T20 | Own PR (second full pipeline, heaviest integration) |
| M12: Human Gate Commands | T21, T22, T23, T24 | Single PR (all small, same pattern) |
| M13: Status & Recovery | T25, T26 | 1 PR each |
| M14: Loop | T27 | Own PR (integration capstone) |

## Risks

1. **T05 (init) is the first real integration test.** Config, DB, and filesystem
   all meet here. Expect at least one round of fixes to T02/T03/T04 interfaces.

2. **T15 (ticket run) must factor pipeline logic into reusable functions.** T26
   (resume) and T27 (loop) both need to call the post-run pipeline without going
   through the CLI entry point. If T15 bakes logic into the click handler, T26
   and T27 will force a refactor. Same applies to T20.

3. **T13 depends on T17 for validation.** T17 must land before T13. The
   milestone grouping enforces this (M6 before M7), but implementers should be
   aware that T13 cannot be started until T17's `validate_review_result` is
   available to import.

4. **T20 is the heaviest integration ticket.** It touches 6 prior modules (T13,
   T15, T16, T17, T18, T19). This is where most bugs will surface. Plan extra
   review time.

5. **Real Claude Code envelope format may drift.** Captured fixtures in
   `tests/fixtures/` are point-in-time snapshots. If Claude Code changes its
   JSON envelope format, adapter tests may become stale. Keep fixture files
   clearly versioned and adapter parsing defensive.

6. **`activity.log` format is locked but helper is undefined.** T05 introduces
   the log helper, but the format must be consistent across all later tickets
   (T06, T08, T15, T20, T24). Define the helper API clearly in T05 so later
   tickets just call it.

7. **Cycle vs retry counter semantics are subtle.** The distinction (cycles =
   implement-review loops, retries = attempts within a single step) is
   load-bearing for state machine correctness. T09 tests should cover edge
   cases: cycle limit hit during revise, retry limit hit during implementation,
   reset behavior on unblock.

8. **CI scope should stay deterministic.** Use captured fixtures and local
   smoke tests only. Do not introduce live Claude Code invocations in CI.
