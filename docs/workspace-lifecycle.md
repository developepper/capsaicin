# Workspace Lifecycle

This document defines the lifecycle states, transitions, and failure taxonomy
for isolated execution workspaces managed by capsaicin.

## Overview

A **workspace** is a git worktree created for an isolated agent execution
context.  Each workspace is tied to exactly one ticket or epic (XOR constraint)
and tracks the full lifecycle from creation through cleanup.

## Lifecycle States

```
pending ──► setting_up ──► active ──► tearing_down ──► cleaned
   │             │            │             │
   └─► failed ◄──┘            └─► failed ◄──┘
```

| State          | Description                                                     |
|----------------|-----------------------------------------------------------------|
| `pending`      | Workspace record created; git worktree not yet provisioned.     |
| `setting_up`   | Worktree creation and branch checkout in progress.              |
| `active`       | Worktree exists and is ready for agent execution.               |
| `tearing_down` | Cleanup (worktree removal, branch deletion) in progress.        |
| `cleaned`      | Worktree and branch fully removed; terminal success state.      |
| `failed`       | Workspace could not be set up or cleaned up; see failure reason. |

### Terminal States

- `cleaned` — successful completion of the full lifecycle.
- `failed` — requires operator intervention or automated recovery.

## Failure Reasons

When a workspace enters the `failed` state, exactly one `failure_reason` is
recorded.  This invariant is enforced at the storage level: a CHECK constraint
requires `failure_reason IS NOT NULL` when `status = 'failed'` and
`failure_reason IS NULL` when `status != 'failed'`.
The `failure_detail` column may carry additional diagnostic text.

| Reason              | Trigger                                                                   | Recovery Guidance                                               |
|---------------------|---------------------------------------------------------------------------|-----------------------------------------------------------------|
| `dirty_base_repo`   | The base repository has uncommitted changes that prevent worktree setup.  | Commit or stash changes in the base repo, then retry.           |
| `missing_worktree`  | The expected worktree path does not exist on disk (removed externally).   | Re-create the workspace or mark the run as failed.              |
| `branch_drift`      | The target branch has diverged from the expected base ref.                | Rebase or reset the branch, or create a new workspace.          |
| `setup_failure`     | `git worktree add` or branch creation failed for an infrastructure reason.| Check disk space, permissions, and git state, then retry.       |
| `cleanup_conflict`  | Worktree removal failed (locked files, modified tracked files, etc.).     | Manually remove the worktree directory, then mark as cleaned.   |

## State Transitions

### Happy Path

1. **Create** — orchestrator inserts a `pending` workspace row.
2. **Set up** — `pending` → `setting_up`: run `git worktree add` with the
   configured `branch_prefix` and `base_ref`.
3. **Activate** — `setting_up` → `active`: worktree verified on disk; agent
   runs may now reference this workspace via `agent_runs.workspace_id`.
4. **Tear down** — `active` → `tearing_down`: after the ticket/epic workflow
   completes or is abandoned, begin cleanup.
5. **Clean** — `tearing_down` → `cleaned`: worktree removed, branch deleted
   (if `auto_cleanup` is enabled).

### Failure Transitions

- `pending` → `failed` with `dirty_base_repo`: base repo has uncommitted
  changes detected during pre-setup check.
- `setting_up` → `failed` with `setup_failure`: `git worktree add` exits
  non-zero.
- `setting_up` → `failed` with `branch_drift`: branch already exists and has
  diverged from the expected base.
- `active` → `failed` with `missing_worktree`: health check finds the
  worktree path absent.
- `active` → `failed` with `branch_drift`: periodic or pre-run check detects
  the base branch has moved beyond the recorded `base_ref`.
- `tearing_down` → `failed` with `cleanup_conflict`: `git worktree remove`
  fails due to locked or modified files.

## Linkage

- `workspaces.ticket_id` / `workspaces.epic_id` — XOR FK linking the
  workspace to the work item it serves.
- `agent_runs.workspace_id` — nullable FK added by migration 0012; links each
  agent run to the workspace it executed in.  Runs that predate workspace
  isolation have `workspace_id = NULL`.  INSERT/UPDATE triggers enforce that
  the linked workspace belongs to the same ticket or epic as the run.

## Configuration

Workspace isolation is controlled by the optional `[workspace]` config section:

```toml
[workspace]
enabled = true
branch_prefix = "capsaicin/"
auto_cleanup = true
worktree_root = "/custom/path/for/worktrees"
```

- `enabled` (default `false`) — when `false`, the orchestrator skips workspace
  creation entirely and runs in the shared worktree as before.
- `branch_prefix` (default `"capsaicin/"`) — prefix for worktree branch names
  (e.g. `capsaicin/ticket-T01`).
- `auto_cleanup` (default `true`) — when `true`, cleaned workspaces have their
  branches deleted automatically during teardown.
- `worktree_root` (default `null`) — optional override for the directory that
  holds isolated worktrees. When unset, capsaicin uses
  `~/.capsaicin/worktrees/<repo-hash>/`.

When the `[workspace]` section is absent from `config.toml`, isolation is
disabled and existing projects behave identically to before.
