-- Item-level description failures must never block a whole query.
-- retry_at NULL = permanently unavailable (item gone or retries exhausted).
CREATE TABLE IF NOT EXISTS description_failures (
    site TEXT NOT NULL,
    site_listing_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    retry_at INTEGER,
    failed_at INTEGER NOT NULL,
    PRIMARY KEY(site,site_listing_id)
);
-- Items observed during a baseline but not yet verifiable: if they later match,
-- they join the baseline silently instead of being notified as new.
CREATE TABLE IF NOT EXISTS description_deferred (
    query_id INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    site_listing_id TEXT NOT NULL,
    PRIMARY KEY(query_id,site_listing_id)
);
ALTER TABLE scan_coverage ADD COLUMN description_skipped INTEGER NOT NULL DEFAULT 0;
