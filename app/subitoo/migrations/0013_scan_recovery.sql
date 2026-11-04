-- Durable retry policy is separate from query definitions and seen history.
CREATE TABLE query_recovery (
 query_id INTEGER PRIMARY KEY REFERENCES queries(id) ON DELETE CASCADE,
 error_code TEXT NOT NULL,
 failure_count INTEGER NOT NULL,
 retry_at INTEGER,
 updated_at INTEGER NOT NULL
);
CREATE TABLE site_backoff (
 site TEXT PRIMARY KEY,
 retry_at INTEGER NOT NULL
);
-- Retain the observed catalog while bounded description batches are processed.
CREATE TABLE description_scans (
 query_id INTEGER PRIMARY KEY REFERENCES queries(id) ON DELETE CASCADE,
 snapshot_json TEXT NOT NULL,
 coverage_json TEXT NOT NULL,
 expanded_baseline INTEGER NOT NULL,
 scanned_at INTEGER NOT NULL
);
