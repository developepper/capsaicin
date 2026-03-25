-- Role override storage for per-epic and per-ticket adapter selection.
-- Epic overrides are scoped to planner/planning_reviewer roles;
-- ticket overrides are scoped to implementer/reviewer roles.

CREATE TABLE IF NOT EXISTS role_overrides (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    epic_id    TEXT REFERENCES planned_epics(id),
    ticket_id  TEXT REFERENCES tickets(id),
    role       TEXT NOT NULL CHECK (role IN (
                   'implementer','reviewer','planner','planning_reviewer'
               )),
    backend    TEXT NOT NULL,
    command    TEXT NOT NULL,
    model      TEXT,
    allowed_tools TEXT,  -- JSON array, e.g. '["Read","Grep"]'
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- Exactly one scope must be set: epic_id XOR ticket_id
    CHECK (
        (epic_id IS NOT NULL AND ticket_id IS NULL)
        OR (epic_id IS NULL AND ticket_id IS NOT NULL)
    ),

    -- Epic overrides only for planner/planning_reviewer
    -- Ticket overrides only for implementer/reviewer
    CHECK (
        (epic_id IS NOT NULL AND role IN ('planner', 'planning_reviewer'))
        OR (ticket_id IS NOT NULL AND role IN ('implementer', 'reviewer'))
    ),

    -- One override per scope + role
    UNIQUE (epic_id, role),
    UNIQUE (ticket_id, role)
);

CREATE INDEX IF NOT EXISTS idx_role_overrides_epic
    ON role_overrides(epic_id);

CREATE INDEX IF NOT EXISTS idx_role_overrides_ticket
    ON role_overrides(ticket_id);

CREATE INDEX IF NOT EXISTS idx_role_overrides_project
    ON role_overrides(project_id);
