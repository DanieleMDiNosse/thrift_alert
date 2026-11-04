-- Keep product reviews separate from historical price/deal judgments.
CREATE TABLE ai_listing_reviews (
 target_id TEXT PRIMARY KEY,
 site TEXT NOT NULL,
 site_listing_id TEXT NOT NULL,
 request_fingerprint TEXT NOT NULL,
 request_token TEXT NOT NULL,
 snapshot_json TEXT NOT NULL,
 job_id INTEGER REFERENCES ai_jobs(id) ON DELETE SET NULL,
 result_json TEXT,
 sources_json TEXT NOT NULL DEFAULT '[]',
 result_snapshot_json TEXT,
 result_fingerprint TEXT,
 analyzed_at INTEGER,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 UNIQUE(site,site_listing_id)
);
