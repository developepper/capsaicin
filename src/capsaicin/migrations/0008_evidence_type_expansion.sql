-- No-op: evidence_type CHECK constraint now includes all six canonical types
-- in 0005_backend_evidence.sql directly. This migration previously expanded
-- the constraint but is no longer needed.
SELECT 1;
