# Catalog data and marketplace visibility correction

## Observed failure

A live read-only audit found 190 stored and API-visible listings: Subito 100,
Vinted 83, Wallapop 7. This was not a Vinted ingestion outage. The default
all-categories API correctly kept rankings separate, but the frontend ignored
`rankings.by_category`, fell back to bare products and lost computed scores,
listing links, prices and marketplace context. Ranked views also omitted all
unranked products rather than explaining why they were not eligible.

The AI listing view initialized condition/shipping/fees as unknown and applied only
manual edits. There were zero manually entered fact records. Raw Subito features
already included condition and some exact shipping quotes; Vinted payloads included
condition labels and exact service fees. Those existing fields were discarded.

## Implemented corrections

- Render category-local rankings, enriched with actual listing evaluations, under
  separate category headings. Preserve unranked products as inspectable records,
  without assigning them a numerical position in the supported ranking.
- Display exact per-marketplace listing counts and an AI marketplace filter.
  Product cards include marketplace, price, original link, condition/cost fields
  and other observed listings for that product.
- Read explicit Subito condition/shipping fields and Vinted condition/service-fee
  fields from existing raw records. Preserve condition labels and field provenance.
  These are marketplace/seller declarations, not inspections. Manual values retain
  precedence, including an explicit unknown. Currency mismatches, malformed raw
  data, missing values and invalid amounts do not become guessed facts.
- Canonicalize the explicit speaker/speakers/passive_bookshelf_speakers aliases to
  the existing audio policy; do not classify fragrance diffusers or accessories
  as audio. Existing product IDs/raw identity records are not rewritten.
- Preserve the strict complete-cost value score. Add a separate asking-price-only
  indicator (quality × condition factor × 100 / asking price), with currency
  separation, finite-value checks and explicit exclusions. It is not a total-cost
  estimate or verified bargain. The value view uses this clearly labelled indicator
  only when no complete-cost ranking is available for that category/filter.
- Missing market-deal scores remain missing: at least five other comparable
  asking-price observations are required. No fictitious transaction prices.

No database migration, raw listing overwrite, marketplace rescans, notification
sends, bulk research refresh or additional model server was needed for these fixes.
GUI/AI worker were reloaded; the existing monitoring process was preserved.

## Live read-back

On the audited dataset, after reloading:

| Platform | Listings | Condition available | Shipping known | Fees known |
|---|---:|---:|---:|---:|
| Subito | 100 | 99 | 50 | 0 |
| Vinted | 83 | 83 | 0 | 83 |
| Wallapop | 7 | 0 | 0 | 0 |

26 products still have sufficient rubric evidence for a quality score; there are
26 asking-price indicators. This is not solved by filling unknowns with invented
ratings. Most remaining missing quality scores require better product-specific
evidence/identification. Wallapop's small collected result set is a separate
search-collection issue, not fixed by rendering more cards.

A live Firefox check displayed 83 Vinted cards and 7 Wallapop cards after selecting
each marketplace. It also verified the asking-price indicator view and no JavaScript
errors. Deterministic browser/backend regression suite: 462 passed, 3 skipped.
Tests include mixed-category API data, preservation of unranked products, marketplace
filtering, malformed raw payloads, known fee currency, manual overrides, category
alias integrity and missing complete-cost behavior.

## Main dashboard follow-up (not just the AI workspace)

Independent inspection reproduced a second visibility defect: `/api/state` selected
only 40 rows globally, and the initial UI showed eight of those. On the audited DB
all 40 selected rows were Subito despite 83 Vinted and 7 Wallapop records existing.
The backend now exposes bounded 40-row pages with real deduplicated totals and
per-site counts; `listing_site` is applied before pagination and `listing_offset`
selects the page. Query parameters are validated, ordering has a deterministic
tie-breaker, and stale offsets recover to the last existing page.

The overview explicitly balances sites by their within-site recency position. It
is not a global chronological ranking. A site-specific view is ordered by latest
observation and listing ID. Each request uses one SQLite read snapshot; independent
pages can change as the live archive changes. This is live paging, not an immutable
export. The frontend has marketplace selection, previous/next controls and the
honest label "Espandi pagina", not a misleading archive-wide "Mostra tutti".

Live read-back: first page 17 Subito / 16 Vinted / 7 Wallapop, all 190 distinct
archived advertisements reachable through pagination. The first eight visible
cards include all three sites. Selecting Wallapop displays all seven records.
Existing professional/user research summaries are now displayed separately in
product details. Final `hermes verify --json`: 471 passed, 3 skipped, build and GUI
readiness HTTP 200 successful. Ruff and whitespace checks passed. Only the GUI
was reloaded for this follow-up; monitor and AI-worker PIDs were preserved.

## Remaining collection limitations, not silently "fixed"

The saved watch still uses the explicit OR alternatives `casse attive`,
`diffusori attivi`, `diffusori`, with no exclusions. The last broad alternative
admits fragrance diffusers, passive speakers and stands. A narrow intended active
speaker search should instead explicitly include `cassa attiva` and retain
audio-specific phrases; users may add translated alternatives for foreign titles.
Saved search semantics, seen history and notification baselines were not changed.

Read-only checks found 38 successful Vinted and 38 successful Wallapop runs and no
recorded errors for either. That proves successful scans, not complete coverage.
Current scans are partial: Subito 27/30 native pages, Vinted 9/10, Wallapop 10/25.
The static per-phrase allocation does not reclaim unused budget (Wallapop searches
used 1, 1 and 8 pages). Identical Italian phrases plus exact title filters can miss
relevant foreign-language advertisements. Rejected/raw result counts are not
persisted, so the number lost to these causes cannot be reconstructed. Fetching,
budget allocation and saved filters were not changed in this UI/data correction;
no live marketplace rescans or notifications were triggered to inflate counts.
