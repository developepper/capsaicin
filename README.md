# capsaicin

`capsaicin` is a local-first autonomous ticket loop for AI-assisted software
development.

It is designed for developers who want more than "run an agent on a task." The
goal is to support a continuous workflow where planning, implementation, review,
revision, and human feedback happen in a controlled loop until a ticket is
actually ready to move forward.

`capsaicin` is aimed at the orchestration gap:

- implementation should not advance without independent review
- review findings should feed back into the loop automatically
- the system should preserve state locally so it can resume cleanly
- a human should remain the final gate for ambiguous decisions and merge
  readiness

## Current Direction

The current design direction is:

- local-first
- SQLite-backed workflow state
- human-readable renders and exports
- `Claude Code` as the strongest first reviewer backend
- `Codex` and `Claude Code` both viable as implementers
- implementation-loop-first MVP, planning loop second

## Docs

The detailed design and implementation specs live in [docs/README.md](./docs/README.md).

Recommended reading order:

1. [docs/overview.md](./docs/overview.md)
2. [docs/architecture.md](./docs/architecture.md)
3. [docs/adapters.md](./docs/adapters.md)
4. [docs/data-model.md](./docs/data-model.md)
5. [docs/state-machine.md](./docs/state-machine.md)
6. [docs/configuration.md](./docs/configuration.md)
7. [docs/cli.md](./docs/cli.md)

## MVP

The MVP is intentionally narrow:

- initialize a local project
- add tickets manually
- run implementer and reviewer loops against one ticket at a time
- persist findings, decisions, retries, and human gates locally
- stop at `human-gate`, never auto-approve

Planning-loop automation, GitHub issue creation, and richer exports come after
the implementation loop is validated.
