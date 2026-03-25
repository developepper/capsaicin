# Adapter Diversification with Evidence-Driven Backend Validation

## Summary

Broaden capsaicin beyond Claude-only by adding a second backend adapter, fully independent four-role backend configuration (planner, planning reviewer, implementer, reviewer), a layered adapter resolution service with ticket → epic → project config → fallback precedence, and first-class UI workflows for capturing and using operator-supplied backend validation evidence (commands, outputs, behavioral notes) throughout the planning and implementation lifecycle. Includes an explicit clarification workflow where adapter-focused epics gate at human-gate when required evidence is missing, present the operator with exact CLI commands to run for validation, and let them paste results directly into the epic detail view before resuming.

## Success Outcome

An operator can configure independent backends for planner, planning reviewer, implementer, and reviewer roles in config.toml, override those assignments at epic or ticket level with deterministic precedence resolution (epic-level for planner/planning_reviewer, ticket-level for implementer/reviewer), create an adapter-focused epic, record structured backend validation evidence in the UI, see missing evidence surfaced as blockers with suggested CLI commands to run, paste validation output directly into the epic, and have at least one non-Claude backend planned and wired through the adapter model — all while the existing Claude workflow continues to work and each role can be independently assigned to a backend.

## Tickets

| # | Title | Dependencies |
|---|-------|--------------|
| [T01](T01.md) | Backend validation evidence data model and migration | - |
| [T02](T02.md) | Four-role config schema and adapter registry | - |
| [T03](T03.md) | Role override storage and adapter resolution service | T02 |
| [T04](T04.md) | UI affordances for capturing backend validation evidence with clarification workflow | T01 |
| [T05](T05.md) | UI for viewing and editing role/backend assignments | T03 |
| [T06](T06.md) | Implement a second backend adapter (OpenAI Codex CLI) | T02 |
| [T07](T07.md) | Inject validation evidence into planning, implementation, and review prompts with suggested requirements | T01, T04 |
| [T08](T08.md) | Missing-evidence gating and blocker states with clarification prompts | T01, T04, T07 |
| [T09](T09.md) | Evidence audit trail and inspectability | T01, T04, T07 |

## Sequencing Notes

Tickets 1 and 2 are independent foundations and can be worked in parallel. Ticket 1 (evidence data model) unlocks tickets 4, 7, 8, and 9 which form the evidence workflow chain. Ticket 2 (four-role config and registry) unlocks ticket 3 (resolution service with overrides) and ticket 6 (Codex adapter) independently. Ticket 3 (resolution service) unlocks ticket 5 (override UI). The critical path for the role-resolution feature is 2 → 3 → 5. The critical path for the evidence-driven clarification flow is 1 → 4 → 7 → 8. Ticket 9 (audit trail) can proceed as soon as 1, 4, and 7 are done, in parallel with ticket 8. Ticket 6 (Codex adapter) is independently implementable after ticket 2 and does not block the evidence workflow or role resolution. The recommended order for a single implementer is: 1, 2, 3, 4, 5, 6, 7, 8, 9.
