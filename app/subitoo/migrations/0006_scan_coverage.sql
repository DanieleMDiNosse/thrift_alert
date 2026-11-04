-- Last successful scan's observed coverage, not an assertion of global completeness.
CREATE TABLE IF NOT EXISTS scan_coverage (
    query_id INTEGER PRIMARY KEY REFERENCES queries(id) ON DELETE CASCADE,
    coverage_json TEXT NOT NULL,
    scanned_at INTEGER NOT NULL
);
