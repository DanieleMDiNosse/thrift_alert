-- AI is additive: original marketplace rows remain authoritative and untouched.
CREATE TABLE ai_settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER, weights_json TEXT NOT NULL DEFAULT '{}');
INSERT INTO ai_settings(id) VALUES(1);
CREATE TABLE ai_products (
 id TEXT PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE, category TEXT NOT NULL,
 data_json TEXT NOT NULL, research_status TEXT NOT NULL DEFAULT 'pending',
 research_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE ai_listing_products (
 listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
 product_id TEXT NOT NULL REFERENCES ai_products(id), confidence REAL NOT NULL
);
CREATE INDEX ai_listing_product_idx ON ai_listing_products(product_id);
CREATE TABLE ai_sources (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES ai_products(id) ON DELETE CASCADE,
 url TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, retrieved_at INTEGER NOT NULL,
 publication_date TEXT, kind TEXT NOT NULL DEFAULT 'web', reliability TEXT NOT NULL DEFAULT 'unassessed',
 UNIQUE(product_id,url)
);
CREATE TABLE ai_conversations (
 id TEXT PRIMARY KEY, product_ids_json TEXT NOT NULL, watch_id INTEGER,
 revision INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE ai_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
 role TEXT NOT NULL CHECK(role IN ('user','assistant')), message TEXT NOT NULL,
 source_ids_json TEXT NOT NULL DEFAULT '[]', created_at INTEGER NOT NULL,
 job_id INTEGER UNIQUE
);
CREATE INDEX ai_messages_conversation_idx ON ai_messages(conversation_id,id);
CREATE TABLE ai_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
 target_id TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, available_at INTEGER NOT NULL,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, error TEXT
);
CREATE UNIQUE INDEX ai_job_active_unique ON ai_jobs(kind,target_id) WHERE status IN ('queued','running');
CREATE INDEX ai_jobs_due_idx ON ai_jobs(status,available_at,id);
