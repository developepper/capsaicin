# Re-seed Commands

This file exists for local testing while the planning-loop work is being built.
If you need to delete the `.capsaicin` project state and recreate the Epic 04
tickets in the database, use the commands below.

The markdown ticket files in this directory remain the source of truth for
scope and implementation details. The DB tickets created here are lightweight
pointers back to those docs so the existing implementation loop can work on
them.

## Assumptions

- you are running commands from the repository root
- the repo path is `.` for `capsaicin init`
- the project name is `capsaicin`
- you want to recreate the Epic 04 tickets after wiping the local DB/project
  state

## Reinitialize Project

If you deleted `.capsaicin/projects/capsaicin/`, recreate it:

```bash
capsaicin init --project "capsaicin" --repo .
```

If you only deleted the database file and left the project directory in place,
`capsaicin init` will fail because the project directory already exists. In
that case, it is usually simpler during testing to remove the whole
`.capsaicin/projects/capsaicin/` directory and run `init` again.

## Add Epic 04 Tickets

This script:

- creates one DB ticket per Epic 04 doc ticket
- captures the generated ticket IDs into shell variables
- recreates the dependency graph from the epic plan

```bash
T01_ID=$(capsaicin ticket add \
  --repo . \
  --title "Epic 04 / T01: Planning Data Model And State Machine Foundation" \
  --description "Implement docs/tickets/epic-04-planning-loop-automation/T01.md. Read the epic README and the ticket references before coding." \
  --criteria "Planning schema, transitions, orchestrator persistence, and lineage are implemented per T01.md." \
  --criteria "Required docs are updated where T01 says the planning model must become concrete." \
  | awk 'NR==1 {print $2}')

T02_ID=$(capsaicin ticket add \
  --repo . \
  --title "Epic 04 / T02: Planner And Planning-Reviewer Contracts" \
  --description "Implement docs/tickets/epic-04-planning-loop-automation/T02.md. Read the epic README, T02 references, and the finalized T01 design first." \
  --criteria "Planner result schema and planning-review result schema are implemented and validated per T02.md." \
  --criteria "Prompt assembly and contract validation cover the fields required for later materialization." \
  | awk 'NR==1 {print $2}')

T03_ID=$(capsaicin ticket add \
  --repo . \
  --title "Epic 04 / T03: Planning Commands, Queries, And CLI Surface" \
  --description "Implement docs/tickets/epic-04-planning-loop-automation/T03.md. Read the epic README, T03 references, and build on T01/T02 outputs." \
  --criteria "Shared planning commands and queries exist for CLI and later UI reuse." \
  --criteria "The CLI exposes the documented capsaicin plan command namespace." \
  | awk 'NR==1 {print $2}')

T04_ID=$(capsaicin ticket add \
  --repo . \
  --title "Epic 04 / T04: Planning Loop Orchestration And Resume" \
  --description "Implement docs/tickets/epic-04-planning-loop-automation/T04.md. Read the epic README, T04 references, and use the T01 orchestrator persistence model." \
  --criteria "capsaicin plan loop drives the bounded planning draft/review/revise cycle per T04.md." \
  --criteria "Resume and activity logging work for interrupted planning runs." \
  | awk 'NR==1 {print $2}')

T05_ID=$(capsaicin ticket add \
  --repo . \
  --title "Epic 04 / T05: Plan Approval And Implementation-Ticket Materialization" \
  --description "Implement docs/tickets/epic-04-planning-loop-automation/T05.md. Read the epic README, T05 references, and use the T01/T02 planning models." \
  --criteria "Approved plans materialize both generated ticket docs and implementation-loop DB ticket records per T05.md." \
  --criteria "Regeneration behavior protects manual edits using the documented hash-gating rules." \
  | awk 'NR==1 {print $2}')

T06_ID=$(capsaicin ticket add \
  --repo . \
  --title "Epic 04 / T06: Planning UI Surfaces" \
  --description "Implement docs/tickets/epic-04-planning-loop-automation/T06.md. Read the epic README, T06 references, and build on the completed planning app/query services." \
  --criteria "The local UI exposes planning views and actions with CLI-parity behavior per T06.md." \
  --criteria "Planning UI actions reuse shared command and query boundaries rather than adding separate workflow logic." \
  | awk 'NR==1 {print $2}')

T07_ID=$(capsaicin ticket add \
  --repo . \
  --title "Epic 04 / T07: Explicit Implementation Completion From pr-ready To done" \
  --description "Implement docs/tickets/epic-04-planning-loop-automation/T07.md. Add an explicit completion action so approved tickets can unblock dependents without requiring GitHub automation." \
  --criteria "There is an explicit CLI and UI path for transitioning pr-ready tickets to done." \
  --criteria "Downstream tickets become runnable only after this explicit completion step." \
  | awk 'NR==1 {print $2}')

echo "T01=$T01_ID"
echo "T02=$T02_ID"
echo "T03=$T03_ID"
echo "T04=$T04_ID"
echo "T05=$T05_ID"
echo "T06=$T06_ID"
echo "T07=$T07_ID"
```

## Recreate Dependencies

Epic 04 dependencies from the current ticket plan:

- T02 depends on T01
- T03 depends on T02
- T04 depends on T03
- T05 depends on T04
- T06 depends on T05
- T07 has no dependencies and can be implemented immediately

Use the captured IDs from the previous step:

```bash
capsaicin ticket dep "$T02_ID" --on "$T01_ID" --repo .

capsaicin ticket dep "$T03_ID" --on "$T02_ID" --repo .

capsaicin ticket dep "$T04_ID" --on "$T03_ID" --repo .

capsaicin ticket dep "$T05_ID" --on "$T04_ID" --repo .

capsaicin ticket dep "$T06_ID" --on "$T05_ID" --repo .
```

## Sanity Check

After re-seeding, confirm the queue:

```bash
capsaicin status --repo .
```

Expected high-level outcome:

- T01 should be the first runnable `ready` ticket
- T02-T06 should remain blocked on their declared dependencies

## Practical Note

Because the current `capsaicin ticket add` command only stores title,
description, and acceptance criteria, keep the detailed implementation context
in the markdown ticket docs and make the DB ticket description point back to
the corresponding file. When working a ticket, read:

1. `docs/tickets/epic-04-planning-loop-automation/README.md`
2. the specific `T0X.md` file
3. every path listed under that ticket's `References`
