# Configuration

`capsaicin` needs explicit project and runtime configuration.

## Source of Truth

`config.toml` on disk is the source of truth for all configuration.
`projects.config` in the database stores a parsed snapshot that is refreshed on
each command invocation. If the two diverge, `config.toml` wins. The database
snapshot exists for convenience during query and prompt assembly, not as an
independent authority.

## Configuration Areas

Likely configuration areas:

- agent selection by role
- adapter command paths
- default repo or workspace paths
- retry limits
- escalation rules
- review policy knobs
- render and export preferences
- GitHub integration settings

## Minimum Viable Default `config.toml`

```toml
[project]
name = "my-project"
repo_path = "."

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

Semantics:

- omitting `model` means "use the CLI default model"
- omitting `allowed_tools` means "use the adapter default behavior"
- reviewer `allowed_tools` maps to Claude Code `--allowed-tools` for the MVP
- prompt assembly is handled by the adapter in MVP; template customization is
  deferred

## MVP Project Resolution

- if `.capsaicin/projects/` contains exactly one project, use it automatically
- if multiple projects exist, require an explicit project selection and error
  otherwise

## Command/Config Dependencies

- `init`: no pre-existing config required
- `ticket add`: no adapter config required
- `ticket dep`: no adapter config required
- `ticket run`: requires implementer adapter config plus timeout and retry
  limits
- `ticket review`: requires reviewer adapter config plus timeout and retry
  limits
- `ticket approve`: no adapter config required
- `ticket revise`: no adapter config required
- `ticket defer`: no adapter config required
- `ticket unblock`: no adapter config required
- `status`: no adapter config required
- `resume`: requires whichever adapter config is needed for the interrupted
  step
- `loop`: requires both implementer and reviewer adapter config plus cycle
  limits

## Non-Goal For MVP

Do not add `auto_approve_clean_pass` to the default config. The human gate is a
core design principle for MVP, not an optional default behavior.
