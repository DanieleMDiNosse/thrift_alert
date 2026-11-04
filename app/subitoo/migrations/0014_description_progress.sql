-- Description progress is distinct from scan success/coverage.
ALTER TABLE description_scans ADD COLUMN pending_count INTEGER NOT NULL DEFAULT 0;
