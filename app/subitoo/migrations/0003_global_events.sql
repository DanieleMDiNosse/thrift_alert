-- Existing queries retain their historical price-change behavior; new ones opt out.
ALTER TABLE queries ADD COLUMN notify_on_price_change INTEGER NOT NULL DEFAULT 0;
UPDATE queries SET notify_on_price_change=1;

CREATE TABLE IF NOT EXISTS listing_events (
    site TEXT NOT NULL,
    site_listing_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('baseline','pending','sent')),
    first_seen_at INTEGER NOT NULL,
    PRIMARY KEY(site, site_listing_id)
);
-- Preserve the old database's seen set, including searches with different schedules.
INSERT OR IGNORE INTO listing_events(site,site_listing_id,state,first_seen_at)
SELECT q.site,l.site_listing_id,'baseline',MIN(l.first_seen_at)
FROM listings l JOIN queries q ON q.id=l.query_id GROUP BY q.site,l.site_listing_id;
