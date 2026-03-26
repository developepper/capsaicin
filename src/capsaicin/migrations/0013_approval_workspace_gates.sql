-- Approval workspace gates: persist workspace divergence events detected
-- at approval time and capture git metadata for downstream commit/PR workflows.

CREATE TABLE IF NOT EXISTS workspace_divergences (
    id              TEXT PRIMARY KEY,
    ticket_id       TEXT NOT NULL REFERENCES tickets(id),
    workspace_id    TEXT REFERENCES workspaces(id),
    expected_diff   TEXT,
    actual_diff     TEXT,
    divergence_type TEXT NOT NULL CHECK (divergence_type IN (
        'diff_mismatch', 'workspace_invalid'
    )),
    recovery_action TEXT NOT NULL CHECK (recovery_action IN (
        'rejected', 'force_override'
    )),
    detected_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approval_metadata (
    decision_id    TEXT PRIMARY KEY REFERENCES decisions(id),
    workspace_id   TEXT REFERENCES workspaces(id),
    branch_name    TEXT NOT NULL,
    worktree_path  TEXT NOT NULL,
    commit_ref     TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_workspace_divergences_ticket
    ON workspace_divergences(ticket_id);
