-- Cache timestamps independent from status updates; optional human-verified facts.
ALTER TABLE ai_products ADD COLUMN researched_at INTEGER;
UPDATE ai_products SET researched_at=updated_at WHERE research_status='ready';
CREATE TABLE ai_listing_facts (
 listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
 data_json TEXT NOT NULL, updated_at INTEGER NOT NULL
);
-- Retain earlier retrieved versions for persistent conversation citations.
ALTER TABLE ai_sources RENAME TO ai_sources_legacy;
CREATE TABLE ai_sources (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES ai_products(id) ON DELETE CASCADE,
 url TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, retrieved_at INTEGER NOT NULL,
 publication_date TEXT, kind TEXT NOT NULL DEFAULT 'web', reliability TEXT NOT NULL DEFAULT 'unassessed'
);
INSERT INTO ai_sources SELECT * FROM ai_sources_legacy;
DROP TABLE ai_sources_legacy;
CREATE INDEX ai_sources_product_idx ON ai_sources(product_id,retrieved_at);
