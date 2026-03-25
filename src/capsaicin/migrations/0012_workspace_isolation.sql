-- Workspace isolation: dedicated persisted workspace entity and linkage to
-- agent_runs.  Tracks git worktree lifecycle per ticket/epic without
-- overloading the free-form orchestrator_state.resume_context.

CREATE TABLE IF NOT EXISTS workspaces (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    ticket_id       TEXT REFERENCES tickets(id),
    epic_id         TEXT REFERENCES planned_epics(id),
    worktree_path   TEXT NOT NULL,
    branch_name     TEXT NOT NULL,
    base_ref        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending','setting_up','active',
                        'tearing_down','cleaned','failed'
                    )),
    failure_reason  TEXT CHECK (failure_reason IN (
                        'dirty_base_repo','missing_worktree',
                        'branch_drift','setup_failure',
                        'cleanup_conflict'
                    )),
    failure_detail  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    -- XOR: exactly one of ticket_id or epic_id must be set.
    CHECK (
        (ticket_id IS NOT NULL AND epic_id IS NULL) OR
        (ticket_id IS NULL AND epic_id IS NOT NULL)
    ),
    -- Failed workspaces must record a failure_reason;
    -- non-failed workspaces must not have one.
    CHECK (
        (status = 'failed' AND failure_reason IS NOT NULL) OR
        (status != 'failed' AND failure_reason IS NULL)
    )
);

-- Link agent_runs to the workspace they executed in.
ALTER TABLE agent_runs ADD COLUMN workspace_id TEXT REFERENCES workspaces(id);

-- Enforce that a workspace's project_id matches the owning ticket/epic's
-- project_id.  SQLite CHECK constraints cannot reference other tables, so we
-- use triggers.

CREATE TRIGGER IF NOT EXISTS trg_workspaces_project_coherence_insert
BEFORE INSERT ON workspaces
BEGIN
    SELECT RAISE(ABORT, 'workspace project_id does not match the linked ticket/epic project')
    WHERE NOT EXISTS (
        SELECT 1
        WHERE (NEW.ticket_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM tickets
                           WHERE id = NEW.ticket_id AND project_id = NEW.project_id))
           OR (NEW.epic_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM planned_epics
                           WHERE id = NEW.epic_id AND project_id = NEW.project_id))
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_workspaces_project_coherence_update
BEFORE UPDATE ON workspaces
BEGIN
    SELECT RAISE(ABORT, 'workspace project_id does not match the linked ticket/epic project')
    WHERE NOT EXISTS (
        SELECT 1
        WHERE (NEW.ticket_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM tickets
                           WHERE id = NEW.ticket_id AND project_id = NEW.project_id))
           OR (NEW.epic_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM planned_epics
                           WHERE id = NEW.epic_id AND project_id = NEW.project_id))
    );
END;

-- Enforce that a run's workspace belongs to the same ticket or epic as the run.

CREATE TRIGGER IF NOT EXISTS trg_agent_runs_workspace_coherence_insert
BEFORE INSERT ON agent_runs
WHEN NEW.workspace_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'workspace does not belong to the same ticket/epic as the run')
    WHERE NOT EXISTS (
        SELECT 1 FROM workspaces w
        WHERE w.id = NEW.workspace_id
          AND COALESCE(w.ticket_id, '') = COALESCE(NEW.ticket_id, '')
          AND COALESCE(w.epic_id, '') = COALESCE(NEW.epic_id, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_runs_workspace_coherence_update
BEFORE UPDATE OF workspace_id ON agent_runs
WHEN NEW.workspace_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'workspace does not belong to the same ticket/epic as the run')
    WHERE NOT EXISTS (
        SELECT 1 FROM workspaces w
        WHERE w.id = NEW.workspace_id
          AND COALESCE(w.ticket_id, '') = COALESCE(NEW.ticket_id, '')
          AND COALESCE(w.epic_id, '') = COALESCE(NEW.epic_id, '')
    );
END;

-- Prevent retargeting a run (changing ticket_id or epic_id) when it has a
-- workspace_id, which would bypass the coherence check above.

CREATE TRIGGER IF NOT EXISTS trg_agent_runs_no_retarget_ticket
BEFORE UPDATE OF ticket_id ON agent_runs
WHEN NEW.workspace_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'workspace does not belong to the same ticket/epic as the run')
    WHERE NOT EXISTS (
        SELECT 1 FROM workspaces w
        WHERE w.id = NEW.workspace_id
          AND COALESCE(w.ticket_id, '') = COALESCE(NEW.ticket_id, '')
          AND COALESCE(w.epic_id, '') = COALESCE(NEW.epic_id, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_runs_no_retarget_epic
BEFORE UPDATE OF epic_id ON agent_runs
WHEN NEW.workspace_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'workspace does not belong to the same ticket/epic as the run')
    WHERE NOT EXISTS (
        SELECT 1 FROM workspaces w
        WHERE w.id = NEW.workspace_id
          AND COALESCE(w.ticket_id, '') = COALESCE(NEW.ticket_id, '')
          AND COALESCE(w.epic_id, '') = COALESCE(NEW.epic_id, '')
    );
END;

-- Indexes for common query patterns.
CREATE INDEX IF NOT EXISTS idx_workspaces_project_status
    ON workspaces(project_id, status);

CREATE INDEX IF NOT EXISTS idx_workspaces_ticket
    ON workspaces(ticket_id);

CREATE INDEX IF NOT EXISTS idx_workspaces_epic
    ON workspaces(epic_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_workspace
    ON agent_runs(workspace_id);
