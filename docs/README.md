# Capsaicin Docs

This directory holds the working design and implementation specs for
`capsaicin`.

Current status:

- Epic 01, the implementation-loop MVP, is complete.
- The next planned areas are reliability and diagnostics first, then a UI for
  the existing implementation loop.
- Planning-loop automation remains part of the product direction, but it is not
  the immediate next step.

Recommended reading order:

1. [overview.md](./overview.md)
2. [architecture.md](./architecture.md)
3. [state-machine.md](./state-machine.md)
4. [data-model.md](./data-model.md)
5. [adapters.md](./adapters.md)
6. [configuration.md](./configuration.md)
7. [cli.md](./cli.md)

Document roles:

- `overview.md`: product vision, workflow, actors, and design principles
- `architecture.md`: local-first system shape, storage strategy, and runtime
  direction
- `state-machine.md`: ticket states, transition rules, retries, cycles, human
  gates, dependency behavior, and failure recovery rules
- `data-model.md`: SQLite schema, indexes, and persistence notes
- `adapters.md`: agent invocation model, adapter contract, run envelopes, and
  reviewer isolation rules
- `configuration.md`: MVP config surface and defaults
- `cli.md`: MVP command contract and operator-facing behavior
- `tickets/`: completed and planned implementation epics
