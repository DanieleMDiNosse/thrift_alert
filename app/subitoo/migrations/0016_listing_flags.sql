-- Per-listing user curation flags (starred / hidden) in the local GUI.
CREATE TABLE IF NOT EXISTS listing_flags (
    site TEXT NOT NULL,
    site_listing_id TEXT NOT NULL,
    starred INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(site, site_listing_id)
);
