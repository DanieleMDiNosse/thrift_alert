-- Diagnostic codes never contain raw model responses or credentials.
ALTER TABLE ai_jobs ADD COLUMN error_code TEXT;
ALTER TABLE ai_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ai_jobs_priority_idx ON ai_jobs(status,priority,available_at,id);
