-- Add 'complete' to the decisions CHECK constraint for pr-ready -> done flow.

CREATE TABLE decisions_new (
    id          TEXT PRIMARY KEY,
    ticket_id   TEXT REFERENCES tickets(id),
    epic_id     TEXT REFERENCES planned_epics(id),
    decision    TEXT NOT NULL CHECK (decision IN (
                    'approve','reject','revise','defer','unblock','complete'
                )),
    rationale   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (
        (ticket_id IS NOT NULL AND epic_id IS NULL) OR
        (ticket_id IS NULL AND epic_id IS NOT NULL)
    )
);

INSERT INTO decisions_new (
    id, ticket_id, epic_id, decision, rationale, created_at
)
SELECT
    id, ticket_id, epic_id, decision, rationale, created_at
FROM decisions;

DROP TABLE decisions;
ALTER TABLE decisions_new RENAME TO decisions;
