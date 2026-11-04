-- Facts belong to a marketplace advert, not to one saved-search observation.
CREATE TABLE ai_marketplace_facts (
 site TEXT NOT NULL, site_listing_id TEXT NOT NULL, data_json TEXT NOT NULL,
 updated_at INTEGER NOT NULL, PRIMARY KEY(site,site_listing_id)
);
-- Explicit conflict policy: most recently saved facts win; row ID breaks ties.
INSERT INTO ai_marketplace_facts(site,site_listing_id,data_json,updated_at)
SELECT site,site_listing_id,data_json,updated_at FROM (
 SELECT q.site,l.site_listing_id,f.data_json,f.updated_at,
        ROW_NUMBER() OVER(PARTITION BY q.site,l.site_listing_id
                          ORDER BY f.updated_at DESC,f.listing_id DESC) AS position
 FROM ai_listing_facts f JOIN listings l ON l.id=f.listing_id
 JOIN queries q ON q.id=l.query_id
) WHERE position=1;
