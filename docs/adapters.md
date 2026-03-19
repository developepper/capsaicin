# Adapters

## Agent Invocation Model

This is currently the riskiest design area and should be treated as an early
technical spike.

`capsaicin` is intended to drive local agent CLIs such as `Codex` and
`Claude Code` without requiring API integrations.

Likely options:

- subprocess execution with stdin/stdout capture
- subprocess execution with prompt and result handoff through files
- a hybrid model where the orchestrator writes structured inputs to disk and
  captures a final machine-readable or text result from stdout

Current direction:

- `Codex` is likely a natural subprocess fit, but `capsaicin` should not trust
  stdout as the source of truth for what changed
- `Claude Code` offers a cleaner structured-output path for non-interactive
  execution and is the strongest initial target for reviewer runs
- the orchestrator should capture workspace change evidence itself, especially
  post-run diffs, instead of relying on the agent to describe edits faithfully

Practical implication:

- prefer `Claude Code` as the first reviewer backend
- treat `Codex` as an implementer-first backend in MVP
- avoid relying on natural-language parsing for review verdicts when a
  structured-output path exists

Practical caveats for `Claude Code` reviewer runs:

- `--output-format json` wraps the interaction, so the adapter still needs to
  extract and validate the reviewer's inner JSON result from assistant text
- `--max-turns` should be set intentionally to bound review exploration and
  cost
- review cost is real and should eventually be governed by config defaults such
  as max turns or other budget controls

## Adapter Contract

Adapters should stay thin. Their job is to translate between `capsaicin`'s run
contract and a specific CLI tool.

The orchestrator should provide at least:

- working directory path
- role assignment such as `implementer`, `reviewer`, or `planner`
- assembled task prompt
- diff context when the role is reviewing an existing change set
- context file paths or explicit context payloads
- timeout budget
- constraints such as review scope or read-only expectations

One useful normalization is an explicit execution mode:

- `read-write` for implementers
- `read-only` for reviewers

The adapter should return at least:

- exit status such as success, failure, timeout, contract violation, or parse
  error
- structured result when the CLI supports it
- raw stdout and stderr as fallback evidence
- duration and run metadata

For reviewer runs, the adapter contract should additionally require:

- `verdict`: `pass`, `fail`, or `escalate`
- `confidence`: `high`, `medium`, or `low`
- `findings`: list of structured findings with severity, category,
  description, and optional location
- `scope_reviewed`: structured evidence of what the reviewer actually checked

The orchestrator, not the adapter, should additionally capture:

- post-run git diff or equivalent workspace change evidence
- state transitions
- persistence into the local database
- next-step decisions

Adapters should not decide workflow progression or own cross-ticket state.

## Review Result Schema

```json
{
  "verdict": "pass | fail | escalate",
  "confidence": "high | medium | low",
  "findings": [
    {
      "id": "string",
      "severity": "blocking | warning | info",
      "category": "string",
      "location": "string | null",
      "acceptance_criterion_id": "string | null",
      "description": "string",
      "disposition": "open | fixed | wont_fix | disputed"
    }
  ],
  "scope_reviewed": {
    "files_examined": ["string"],
    "tests_run": true,
    "criteria_checked": [
      {
        "criterion_id": "string",
        "description": "string"
      }
    ]
  }
}
```

Interpretation rules:

- `verdict: pass` may still include `warning` or `info` findings
- `verdict: fail` must include at least one `blocking` finding
- `verdict: escalate` means the reviewer could not complete a reliable review
  without human input
- `confidence: low` forces a human gate even if the verdict is `pass`, with
  `gate_reason = 'low_confidence_pass'`

Finding IDs should be assigned by the orchestrator when findings are persisted.

When a finding references a specific acceptance criterion, the reviewer should
set `acceptance_criterion_id` to the criterion's stable ID. This enables the
orchestrator to update criterion statuses mechanically rather than by
guesswork.

## Review Result Validation

Validation rules for reviewer `structured_result`:

- `verdict: fail` requires at least one `blocking` finding
- `verdict: pass` cannot include any `blocking` findings
- `confidence: high` is invalid if `files_examined` is empty
- `confidence: high` is invalid if acceptance criteria were provided but
  `criteria_checked` is empty
- `criteria_checked` entries must reference valid `criterion_id` values from the
  run request's `acceptance_criteria`
- `acceptance_criterion_id` on a finding, when present, must reference a valid
  criterion ID from the run request
- top-level review result fields must always be present, even when empty or
  false

If validation fails, the adapter should return `exit_status: parse_error` and
preserve the raw output for debugging rather than trying to repair the result.

## Run Envelope

Run request envelope:

```json
{
  "run_id": "string",
  "role": "implementer | reviewer | planner",
  "mode": "read-write | read-only",
  "working_directory": "string",
  "prompt": "string",
  "diff_context": "string | null",
  "context_files": ["string"],
  "acceptance_criteria": [
    {
      "id": "string",
      "description": "string",
      "status": "pending | met | unmet | disputed"
    }
  ],
  "prior_findings": [],
  "timeout_seconds": 0,
  "max_turns": 0,
  "adapter_config": {}
}
```

Run result envelope:

```json
{
  "run_id": "string",
  "exit_status": "success | failure | timeout | contract_violation | parse_error",
  "duration_seconds": 0,
  "raw_stdout": "string",
  "raw_stderr": "string",
  "structured_result": {},
  "adapter_metadata": {}
}
```

Notes:

- `structured_result` is usually `null` for implementer runs and populated for
  reviewer runs
- `diff_context` is usually `null` for implementer runs and populated for
  reviewer runs from orchestrator-captured diff state
- `prior_findings` lets revise and re-review loops operate without hidden
  session history
- `acceptance_criteria` should remain first-class data rather than being buried
  only inside the prompt
- `adapter_metadata` is for tool-specific details such as model, turn count, or
  cost that do not drive workflow state directly

## Fresh Session Requirement

Independent review is load-bearing. A fresh review session means:

- a new process invocation
- no inherited interactive conversation history
- context supplied only from the orchestrator-selected inputs
- explicit role assignment as reviewer
- persisted review output linked to a unique run record

If a reviewer run is marked `read-only`, the orchestrator should verify that no
unexpected tracked-file changes occurred. The check should be baseline-based:

1. capture tracked-file diff state before the reviewer starts
2. capture tracked-file diff state after the reviewer exits
3. compare the two snapshots
4. if the diff changed, mark the run invalid as a contract violation

Reviewer prompts should also explicitly warn against treating commit messages,
inline rationale, or self-justifying artifacts as evidence that the
implementation is correct.

## Diff Basis

The diff basis for implementation change evidence and reviewer scope is
tracked files only, via `git diff HEAD`. This applies to:

- empty-implementation detection (`implementing -> human-gate`)
- `run_diffs` content persisted after an implementation run
- `diff_context` provided to the reviewer
- the review baseline comparison

Untracked files are excluded. If generated or untracked files matter for a
specific project, that can be addressed in a future extension, but the MVP
diff basis is strictly tracked files against HEAD.

## Finding Reconciliation

Findings accumulate across implement-review cycles. Without reconciliation,
revise loops produce ambiguous finding lists where it is unclear which issues
persist and which were resolved.

MVP reconciliation uses a lightweight fingerprint:
`(category, location, description_prefix)` where `description_prefix` is the
first 80 characters of the description, normalized to lowercase with collapsed
whitespace. The prefix disambiguates findings that share the same category and
location (especially when location is null) without requiring full semantic
matching.

Rules:

- on `verdict: pass` after a re-review cycle, the orchestrator marks all prior
  open findings for the ticket as `fixed` with `resolved_in_run` pointing to
  the implementation run that preceded the passing review
- on `verdict: fail` after a re-review cycle, the orchestrator matches incoming
  findings to prior open findings by `(category, location)`:
  - matched: update the prior finding's description and severity, link to the
    new review run
  - unmatched prior findings: mark as `fixed` with `resolved_in_run` pointing
    to the preceding implementation run
  - unmatched new findings: persist as new open findings
- on the first review cycle (no prior findings), all findings are persisted as
  new

This avoids complex semantic matching while keeping the finding list clean
across cycles. The human at `human-gate` can override any disposition.
