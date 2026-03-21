# Epic 02: Reliability And Diagnostics

Improve operator trust in the implementation loop by surfacing blocked agent
execution clearly, validating local setup before expensive runs, and making the
human-gate experience more diagnostic.

This epic is driven by manual validation against a real project where two
implementer runs were persisted as `exit_status: success` and routed to
`empty_implementation` even though Claude Code reported repeated
`permission_denials` and explicitly asked for write access in its result text.

## Execution Strategy

This epic should start with the adapter and schema signals that distinguish a
truly empty implementation from a blocked one. Once that signal exists, the
operator-facing surfaces can report it clearly. Preflight checks can then build
on the same validated assumptions, and the loop-selection fix can land
independently.

Key sequencing decisions:

- T01 should land before T02 and T03 because the operator-facing behavior needs
  a reliable permission-denied signal.
- T02 and T03 can proceed in parallel once T01 is in place.
- T04 can proceed independently of T02/T03.
- T05 depends on T04 because `doctor` should reuse the same check helpers
  rather than duplicate validation logic.
- T06 is independent and can land at any point in the epic.
- T07 should follow T02/T03 so it renders the new diagnostics rather than
  inventing a second format.

## Phased Roadmap

| Phase | Focus | Tickets |
|-------|-------|---------|
| 1 | Adapter Signals | T01 |
| 2 | Empty-Run Diagnostics | T02-T03 |
| 3 | Preflight Checks | T04-T05 |
| 4 | Loop And Status UX | T06-T07 |

## Suggested PR / Milestone Grouping

| Milestone | Tickets | PR Strategy |
|-----------|---------|-------------|
| M1: Adapter Outcome Signals | T01 | Own PR |
| M2: Human-Gate And Logging Diagnostics | T02, T03, T07 | Single PR or T02/T03 together then T07 |
| M3: Preflight Validation | T04, T05 | Single PR |
| M4: Loop Selection Fix | T06 | Own PR or bundle with M2 |

## Risks

1. **Outcome classification changes touch shared contracts.** If
   `permission_denied` becomes a distinct run outcome, update schema, types,
   persistence, and CLI rendering together so the system does not silently
   coerce it back into `success`.

2. **Diagnostics can become noisy if they are too verbose by default.** Human
   gates and `status` should surface the high-signal details first and reserve
   raw envelope detail for verbose views.

3. **Permission checks are environment-sensitive.** Claude tool permissions may
   vary by repo and local configuration. Checks should be explicit about what
   was found and what exact fix is required.

4. **`doctor` should be advisory where possible.** Some checks should warn
   rather than hard-fail, especially dirty working trees or optional tooling.

5. **Loop selection must preserve state-machine expectations.** Expanding
   auto-selection to `revise` tickets must not accidentally bypass dependency or
   cycle-limit behavior.

## Evidence Captured From Manual Validation

- Claude Code permission denials appear at the top level of the outer envelope
  as `permission_denials: []`.
- Each denial entry includes `tool_name`, `tool_use_id`, and `tool_input`.
- For `Edit` and `Write`, `tool_input.file_path` is present.
- For `Bash`, `tool_input.command` is present.
- Denials repeat per attempted tool call rather than being deduplicated.
- Real failing runs still returned `is_error: false` and a clean process exit,
  so process success alone is not a reliable signal that implementation could
  proceed.
- Real agent result text already contained concise remediation guidance such as
  requesting write permission, so the CLI should surface that text rather than
  discarding it.
