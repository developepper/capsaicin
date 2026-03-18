# Overview

`capsaicin` is a local-first autonomous ticket loop for AI-assisted software
development.

It is designed for developers who want more than "run an agent on a task." The
goal is to support a continuous workflow where planning, implementation, review,
revision, and human feedback happen in a controlled loop until a ticket is
actually ready to move forward.

## Problem

Many AI coding workflows automate parts of planning or implementation, but they
usually stop short of the most important quality-control step: independent
review with feedback fed back into the loop before moving on.

That creates a predictable failure mode:

- implementation is generated
- review is manual, inconsistent, or skipped
- missed acceptance criteria survive longer than they should
- the human has to manage the state machine by hand
- progress across tickets becomes tedious and fragile

`capsaicin` is meant to solve the orchestration problem, not just the coding
problem.

## Product Vision

`capsaicin` should be a reusable open-source project that can work across
different repositories and different developers with similar goals.

It should:

- work locally on a developer machine
- support `Codex`, `Claude Code`, or both
- avoid requiring OpenAI or Anthropic API integration
- use structured local state for planning and execution
- support one-ticket-at-a-time implementation with independent review
- stop for human feedback only when needed
- keep persistent local state so work can resume cleanly
- keep workflow state human-inspectable and exportable
- produce output that is ready for GitHub issues and pull requests

It should not:

- replace human judgment
- force a hosted service
- require GitHub to manage early planning
- let the same execution session self-certify completion
- advance to the next ticket without a real quality gate

## Core Workflow

`capsaicin` manages two major loops:

1. planning loop
2. implementation loop

### Planning Loop

The planning loop starts from a problem statement and ends with an approved
local plan that is ready to seed into GitHub issues.

Flow:

1. Human describes the problem or desired outcome.
2. Planner agent drafts an epic and a set of digestible tickets in structured
   local state.
3. Reviewer agent reviews the planning records.
4. If findings exist, the planner revises the plan.
5. Repeat until review returns no blocking findings.
6. Human approves the plan.
7. GitHub epic and ticket issues are created from the approved local plan.

### Implementation Loop

The implementation loop starts from one approved ticket and ends only when that
ticket is PR-ready.

Flow:

1. Select one ticket whose dependencies are satisfied.
2. Implementer agent works the ticket.
3. Reviewer agent reviews the resulting changes.
4. If findings exist, the implementer fixes them.
5. Repeat until review returns no blocking findings.
6. Human performs the final gate.
7. Create or update the pull request.
8. Move to the next ticket only after the current one is actually ready.

## Why This Workflow

Separate review catches real problems:

- missed acceptance criteria
- incomplete deliverables
- hidden regressions
- weak tests
- architecture drift
- scope expansion that should have been split into a follow-up ticket

The workflow already works well manually. The missing piece is automation around
state, handoff, persistence, and escalation.

## Actor Model

- `Human`: sets goals, resolves ambiguity, approves planning, approves merge
  readiness
- `Planner`: drafts and revises epic/ticket planning records
- `Implementer`: makes code and documentation changes for a ticket
- `Reviewer`: critiques planning artifacts or code changes and blocks
  advancement when needed

Recommended dual-agent mode:

- `Codex` for planning or implementation
- `Claude Code` for review

Single-agent mode should still be supported, but review must happen in a
separate fresh session. The same session should not certify its own completion.

## Human Gates

`capsaicin` should not ask the user for routine continuation. It should ask
only when a real decision or blocker exists.

Examples:

- multiple valid scope cuts exist
- a product or architecture tradeoff is required
- acceptance criteria appear incomplete or misleading
- implementation reveals hidden dependencies
- a reviewer recommends splitting scope into a follow-up ticket
- environment issues block meaningful verification

This is supervised autonomy, not blind autonomy.

## Review Policy

Review is a blocking quality gate.

The reviewer should check for:

- correctness
- regressions
- unmet acceptance criteria
- missing tests
- architecture violations
- hidden scope creep
- insufficient ticket definitions

The system should not allow "looks good" reviews to replace actual findings or
explicit no-finding outcomes.

## MVP Direction

The MVP should start with the implementation loop first and add the planning
loop second. The implementation-review-fix cycle is the highest-value part of
the system and the most immediate relief for the current manual workflow pain.

## Design Principles

- local-first over hosted-first
- explicit state over implicit chat history
- one ticket at a time
- independent review before advancement
- human gate on ambiguity and final acceptance
- structured local state with human-readable views
- repo-agnostic workflow
- bounded loops, not endless agent churn
