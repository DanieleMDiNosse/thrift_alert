-- On-demand full descriptions, shared across overlapping queries.
CREATE TABLE IF NOT EXISTS listing_descriptions (
    site TEXT NOT NULL,
    site_listing_id TEXT NOT NULL,
    description TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY(site,site_listing_id)
);
