# Capsaicin Docs

This directory holds the working design and implementation specs for
`capsaicin`.

Recommended reading order:

1. [overview.md](./overview.md)
2. [architecture.md](./architecture.md)
3. [adapters.md](./adapters.md)
4. [data-model.md](./data-model.md)
5. [state-machine.md](./state-machine.md)
6. [configuration.md](./configuration.md)
7. [cli.md](./cli.md)

Document roles:

- `overview.md`: product vision, workflow, actors, and design principles
- `architecture.md`: local-first system shape, storage strategy, and runtime
  direction
- `adapters.md`: agent invocation model, adapter contract, run envelopes, and
  reviewer isolation rules
- `data-model.md`: SQLite schema, indexes, persistence notes, and recovery
- `state-machine.md`: ticket states, transition rules, retries, cycles, and
  human gates
- `configuration.md`: MVP config surface and defaults
- `cli.md`: MVP command contract and operator-facing behavior
