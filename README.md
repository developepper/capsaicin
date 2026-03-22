<p align="center">
  <img src="src/capsaicin/web/static/brand/capsaicin-logo.svg" alt="capsaicin logo" width="420">
</p>

# capsaicin

`capsaicin` is a local-first ticket orchestrator for AI-assisted software
development. It runs an implementation loop around one ticket at a time:
implement, review, revise, stop for human approval, then move on.

The current MVP is built around a local SQLite database, a `.capsaicin/`
project directory inside your repo, and the `Claude Code` CLI as the wired
implementer/reviewer backend.

## What It Does

`capsaicin` helps you:

- initialize a project-local workflow state store
- create tickets and acceptance criteria
- declare ticket dependencies
- run implementation passes
- run independent review passes
- persist findings, retries, and decisions locally
- stop at explicit human gates instead of auto-approving
- resume interrupted work safely

It is designed to orchestrate the workflow, not replace human judgment.

## Current Scope

The implementation-loop MVP is available now.

Included:

- `capsaicin init`
- `capsaicin ticket add`
- `capsaicin ticket dep`
- `capsaicin ticket run`
- `capsaicin ticket review`
- `capsaicin ticket approve`
- `capsaicin ticket revise`
- `capsaicin ticket defer`
- `capsaicin ticket unblock`
- `capsaicin status`
- `capsaicin resume`
- `capsaicin loop`
- `capsaicin ui`

Not in scope yet:

- GitHub issue creation
- pull request creation
- planning-loop automation
- hosted sync

## Requirements

- Python `3.11+`
- `git`
- a git repository to run against
- the `Claude Code` CLI installed and available on `PATH` as `claude`

The `capsaicin ui` command additionally pulls in `starlette`, `jinja2`, `uvicorn`,
and `python-multipart` — all installed automatically via `pip install`.

`capsaicin` captures tracked-file diffs using `git diff HEAD`, so the target
repository should be a normal git worktree.

## Installation

Clone the repo and install it in editable mode:

```bash
git clone <your-fork-or-this-repo>
cd capsaicin
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development tools and tests:

```bash
pip install -e .[dev]
```

Verify the CLI:

```bash
capsaicin --help
capsaicin ticket --help
```

## How Project State Is Stored

When you initialize a repo, `capsaicin` creates a local control directory:

```text
.capsaicin/
  projects/<project-slug>/
    capsaicin.db
    config.toml
    activity.log
    renders/
    exports/
```

Important files:

- `capsaicin.db`: canonical workflow state
- `config.toml`: source-of-truth configuration
- `activity.log`: append-only debug/event log

## Quick Start

Inside the repository you want to manage:

```bash
capsaicin init --project "My Project" --repo .
```

Then add a ticket:

```bash
capsaicin ticket add \
  --title "Add health endpoint" \
  --description "Implement /health and cover it with tests." \
  --criteria "GET /health returns 200" \
  --criteria "Response includes version metadata"
```

Run implementation:

```bash
capsaicin ticket run
```

Run review:

```bash
capsaicin ticket review
```

Inspect status:

```bash
capsaicin status
```

If the ticket reaches `human-gate`, choose one:

```bash
capsaicin ticket approve
capsaicin ticket revise --add-finding "Need stronger test coverage"
capsaicin ticket defer --rationale "Waiting on API decision"
```

## Typical Workflow

The normal operator workflow is:

1. Initialize a project in the repo.
2. Add one or more tickets.
3. Optionally add dependencies.
4. Run `capsaicin ticket run` for a specific ticket or let it auto-select the
   next runnable `ready` ticket.
5. Run `capsaicin ticket review`.
6. If review fails, the ticket moves to `revise`; run `capsaicin ticket run`
   again.
7. If review passes or escalates, the ticket moves to `human-gate`.
8. Make a human decision with `approve`, `revise`, or `defer`.
9. Repeat for the next ticket.

If you prefer an in-process loop, use:

```bash
capsaicin loop
```

That command automatically performs:

- implementation
- review
- re-implementation after review failure
- re-review

It still stops at `human-gate` or `blocked`. It never auto-approves.

## Command Guide

### `capsaicin init`

Initialize the current repo for `capsaicin`:

```bash
capsaicin init --project "My Project" --repo .
```

What it does:

- creates `.capsaicin/projects/<slug>/`
- writes `config.toml`
- creates `capsaicin.db`
- runs schema migrations
- creates `activity.log`
- inserts initial project/orchestrator rows

### `capsaicin ticket add`

Create a ticket inline:

```bash
capsaicin ticket add \
  --title "Implement auth middleware" \
  --description "Add JWT auth to protected routes." \
  --criteria "Requests with valid JWT succeed" \
  --criteria "Expired JWTs are rejected"
```

Or import from TOML:

```bash
capsaicin ticket add --from ticket.toml
```

Example `ticket.toml`:

```toml
title = "Implement user authentication"
description = """
Add JWT-based authentication middleware.
"""

[[criteria]]
description = "Login endpoint returns a valid JWT"

[[criteria]]
description = "Middleware rejects expired tokens"
```

### `capsaicin ticket dep`

Add a dependency:

```bash
capsaicin ticket dep TICKET_ID --on DEPENDENCY_ID
```

The dependent ticket will not run until the dependency is `done`.

### `capsaicin ticket run`

Run the implementation pipeline:

```bash
capsaicin ticket run
capsaicin ticket run TICKET_ID
```

Behavior:

- auto-selects the next runnable `ready` ticket if no ID is provided
- transitions the ticket into implementation
- invokes the implementer adapter
- captures the git diff
- moves the ticket to:
  - `in-review` when changes exist
  - `human-gate` when the implementation produced no tracked-file diff
  - `blocked` when implementation retries are exhausted

### `capsaicin ticket review`

Run the review pipeline:

```bash
capsaicin ticket review
capsaicin ticket review TICKET_ID
capsaicin ticket review --allow-drift
```

Behavior:

- reviews tickets in `in-review`
- checks that the current workspace still matches the implementation diff
- captures a review baseline before invoking the reviewer
- persists findings and acceptance-criteria updates
- moves the ticket to:
  - `revise` when blocking findings exist
  - `human-gate` on pass, escalation, low-confidence pass, or cycle limit
  - `blocked` when review retries are exhausted

Use `--allow-drift` only when you intentionally changed the workspace after the
implementation run and want review to proceed against the new current diff.

### `capsaicin ticket approve`

Approve a ticket at the human gate:

```bash
capsaicin ticket approve
capsaicin ticket approve TICKET_ID
capsaicin ticket approve --rationale "Reviewed manually"
capsaicin ticket approve --force
```

Notes:

- approval normally verifies that the workspace still matches what was reviewed
- `--force` overrides the workspace match check
- `--rationale` is required for some gate reasons such as cycle-limit or
  reviewer escalation

### `capsaicin ticket revise`

Send a ticket back for another implementation pass:

```bash
capsaicin ticket revise
capsaicin ticket revise TICKET_ID --add-finding "Missing migration rollback"
capsaicin ticket revise TICKET_ID --reset-cycles
```

Use this when you want to add explicit human feedback or reset the loop state.

### `capsaicin ticket defer`

Defer or abandon a ticket:

```bash
capsaicin ticket defer TICKET_ID --rationale "Waiting on product decision"
capsaicin ticket defer TICKET_ID --abandon --rationale "Out of scope"
```

- normal defer moves the ticket to `blocked`
- `--abandon` marks it as effectively finished/abandoned

### `capsaicin ticket unblock`

Return a blocked ticket to `ready`:

```bash
capsaicin ticket unblock TICKET_ID
capsaicin ticket unblock TICKET_ID --reset-cycles
```

### `capsaicin status`

Show a project summary:

```bash
capsaicin status
```

Show one ticket in detail:

```bash
capsaicin status --ticket TICKET_ID
capsaicin status --ticket TICKET_ID --verbose
```

Use this often. It is the main operator view into the current workflow state.

### `capsaicin resume`

Recover from an interrupted run:

```bash
capsaicin resume
```

Use this after:

- a crashed terminal
- a killed agent process
- an interrupted machine/session

`capsaicin` uses the persisted orchestrator state and prior run records to
decide whether to continue, retry, mark failure, or stop for human action.

### `capsaicin loop`

Run the full implement-review-revise loop automatically:

```bash
capsaicin loop
capsaicin loop TICKET_ID
capsaicin loop TICKET_ID --max-cycles 2
```

This is the fastest way to operate once your project is configured and you want
the tool to keep driving until a human decision is required.

### `capsaicin ui`

Launch the local operator web UI:

```bash
capsaicin ui
capsaicin ui --port 8080
capsaicin ui --no-open
```

Behavior:

- starts a local HTTP server bound to `127.0.0.1`
- picks an available port automatically unless `--port` is provided
- opens the browser by default; `--no-open` suppresses this
- serves a dashboard with queue state, inbox, and activity
- shows ticket detail with acceptance criteria, findings, diff, and run history
- provides action forms for approve, revise, defer, unblock, run, review, and
  loop directly in the browser
- live updates via server-sent events when ticket or orchestrator state changes
- no authentication, remote access, or multi-user support — this is a
  single-operator local tool

The UI uses the same shared services as the CLI. Actions taken in the browser
produce identical state transitions and persist through the same database.

## Statuses You Will See

Ticket statuses:

- `ready`: queued to be worked
- `implementing`: implementation run in progress
- `in-review`: awaiting or undergoing review
- `revise`: reviewer found blocking issues
- `human-gate`: waiting for a human decision
- `pr-ready`: approved and ready for your normal PR workflow
- `blocked`: cannot proceed automatically
- `done`: completed or abandoned

Common gate reasons:

- `review_passed`
- `low_confidence_pass`
- `reviewer_escalated`
- `cycle_limit`
- `empty_implementation`

## Configuration

Each initialized project gets a `config.toml` like this:

```toml
[project]
name = "my-project"
repo_path = "/absolute/path/to/repo"

[adapters.implementer]
backend = "claude-code"
command = "claude"

[adapters.reviewer]
backend = "claude-code"
command = "claude"
allowed_tools = ["Read", "Glob", "Grep", "Bash"]

[limits]
max_cycles = 3
max_impl_retries = 2
max_review_retries = 2
timeout_seconds = 300

[reviewer]
mode = "read-only"

[ticket_selection]
order = "created_at"

[paths]
renders_dir = "renders"
exports_dir = "exports"
```

Important points:

- `config.toml` is the source of truth
- repo paths are stored as absolute paths
- current shipped defaults assume the `claude` CLI
- reviewer runs are intended to be read-only

## Multi-Project Usage

If `.capsaicin/projects/` contains exactly one project, commands auto-resolve
it.

If there are multiple projects, pass `--project`:

```bash
capsaicin status --project my-project
capsaicin ticket run --project my-project
```

You can also point commands at a repo explicitly:

```bash
capsaicin status --repo /path/to/repo
```

## Practical Advice

- Commit or stash unrelated work before running a ticket when possible.
- Review `capsaicin status` before approving.
- Use `ticket review --allow-drift` only when you intentionally want to review
  the modified workspace, not the original implementation output.
- Use `resume` after interruption instead of guessing what state the tool was
  in.
- Treat `pr-ready` as the end of the MVP automation. PR creation and merge are
  still manual.

## Troubleshooting

### `No projects found`

Run:

```bash
capsaicin init --project "My Project" --repo .
```

### `Multiple projects found`

Pass `--project <slug>`.

### Reviewer or implementer command fails

Check:

- `config.toml`
- that `claude` is installed and on `PATH`
- the target repo path in `[project].repo_path`
- `.capsaicin/projects/<slug>/activity.log`

### Workspace drift errors during review or approval

The current working tree no longer matches the diff captured earlier.

Options:

- review the current state with `capsaicin ticket review --allow-drift`
- or revert/clean your local changes intentionally
- or use `capsaicin ticket approve --force` only when you understand the risk

## Documentation

The design and implementation docs live in [docs/README.md](./docs/README.md).

Recommended order:

1. [docs/overview.md](./docs/overview.md)
2. [docs/architecture.md](./docs/architecture.md)
3. [docs/state-machine.md](./docs/state-machine.md)
4. [docs/data-model.md](./docs/data-model.md)
5. [docs/adapters.md](./docs/adapters.md)
6. [docs/configuration.md](./docs/configuration.md)
7. [docs/cli.md](./docs/cli.md)
