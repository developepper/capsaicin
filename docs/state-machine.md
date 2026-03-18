# State Machine

## Top-Level States

Planning states:

- `planning/new`
- `planning/drafting`
- `planning/in-review`
- `planning/revise`
- `planning/human-gate`
- `planning/approved`
- `planning/blocked`

Ticket states:

- `ticket/ready`
- `ticket/implementing`
- `ticket/in-review`
- `ticket/revise`
- `ticket/human-gate`
- `ticket/pr-ready`
- `ticket/blocked`
- `ticket/done`

## MVP Ticket Transition Rules

Recommended transitions:

- `ready -> implementing`
  trigger: system selects a ticket whose dependencies are satisfied
- `implementing -> in-review`
  trigger: implementer run succeeds and a non-empty `run_diffs` record exists
- `implementing -> human-gate`
  trigger: implementer run succeeds but produces an empty tracked-file diff
- `implementing -> blocked`
  trigger: implementer run fails repeatedly, times out repeatedly, or escalates
- `in-review -> revise`
  trigger: reviewer returns `verdict: fail` with at least one blocking finding
- `in-review -> human-gate`
  trigger: reviewer returns `verdict: pass`, reviewer returns
  `verdict: escalate`, or the cycle limit is reached
- `in-review -> blocked`
  trigger: reviewer run hits repeated `contract_violation` or `parse_error`
- `revise -> implementing`
  trigger: system starts another implementation pass while under the cycle limit
- `revise -> human-gate`
  trigger: cycle limit reached without a clean pass
- `human-gate -> pr-ready`
  trigger: human decision is `approve`
- `human-gate -> revise`
  trigger: human decision is `revise`
- `human-gate -> blocked`
  trigger: human decision is `defer` or `escalate`
- `pr-ready -> done`
  trigger: PR is created and accepted for completion, or later merged
- `blocked -> ready`
  trigger: human explicitly unblocks and requeues the ticket
- `blocked -> done`
  trigger: human rejects or abandons the ticket

## Guard Conditions

- `ready -> implementing` requires all dependencies to be `done`
- `implementing -> in-review` requires a successful run and actual change
  evidence
- `implementing -> human-gate` for empty implementation should set
  `gate_reason = 'empty_implementation'`
- `in-review -> human-gate` on `pass` requires a valid reviewer result with no
  blocking findings
- `in-review -> human-gate` on clean pass should set
  `gate_reason = 'review_passed'`
- `in-review -> human-gate` on reviewer escalation should set
  `gate_reason = 'reviewer_escalated'`
- `in-review -> human-gate` on cycle limit should set
  `gate_reason = 'cycle_limit'`
- `implementing -> blocked` from repeated execution failure should set
  `blocked_reason = 'implementation_failure'`
- `in-review -> blocked` from repeated review contract violations should set
  `blocked_reason = 'reviewer_contract_violation'`
- `revise -> implementing` should increment the cycle counter but not the retry
  counters
- `human-gate -> revise` may reset the cycle counter if the human meaningfully
  re-scopes the work
- `blocked -> ready` should reset cycles only when the human explicitly
  requests it

## Illegal Transitions

- no ticket reaches `pr-ready` without passing through `human-gate`
- no ticket reaches `done` except from `pr-ready` or `blocked`
- no implementation run skips review
- `revise -> pr-ready` is illegal

The orchestrator should enforce this with a simple
`transition_is_legal(from_status, to_status, actor)` function before every
ticket status update.

## Retry And Cycle Model

The retry and cycle model should remain distinct:

- `current_cycle` tracks implement-review loops
- `current_impl_attempt` tracks retries for the current implementation step
- `current_review_attempt` tracks retries for the current review step
- `agent_runs.attempt_number` records the specific attempt number for each run

Cycle limits should send work to `human-gate`. Retry limits should send work to
`blocked`.

## Dependency Handling

The system should support:

- explicit ticket-to-ticket dependencies
- readiness checks before ticket selection
- blocked-state transitions when dependencies are unmet
- detection of invalid dependency graphs such as cycles

Circular dependencies should be treated as planning errors and escalated rather
than worked around implicitly.

Dependency satisfaction is a guard condition on `ready -> implementing`.

## Failure Recovery

The orchestrator should persist enough state to support safe resume:

- run start and end status
- partial outputs when available
- last successful state transition
- whether the ticket is safe to retry automatically
- whether human intervention is required

`blocked` is not enough by itself. The implementation should distinguish
between recoverable run failure, review-blocked work, and true human-decision
blockers.
