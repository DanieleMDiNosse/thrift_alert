# Wallapop adapter evidence and constraints

## Inspected references

- Duccioo/WallaPy commit `c122f73fb6a47137b64669454a71e6d148ba6b37`, Apache-2.0.
- jaimeberdejo/wallapop-mcp commit `214001ef4697a55f8453ad3ee5c997bcd7d9663c`, MIT.
- Inspected the response envelope and parameter/pagination ideas; neither is a runtime
  dependency. No Node/MCP/LLM required. Existing project remains GPL-3.0-or-later.
- Both references use `data.section.payload.items`. Pagination differs:
  WallaPy start_cursor vs MCP meta.next_page. This adapter uses opaque next_page.
  WallaPy's explicitly placeholder time_filter was deliberately excluded.

## Search contract

Paste an HTTPS Wallapop `/app/search` URL with explicit latitude and longitude.
Supported parameters: keywords, latitude, longitude, category_ids, min_sale_price,
max_sale_price, distance, order_by, source, filters_source. Unknown/duplicate
parameters fail rather than silently widening the query. No implicit geographic
location, category translations or invented relative-time filters.

Supported order_by values: newest, price_low_to_high, price_high_to_low. Prefer
newest for monitoring; a price-sorted bounded scan can miss newly posted results.
No default sort is injected into pasted searches. Pages are bounded 1–5 (watch
configuration is stricter: at most 3), HTTP timeout 20 seconds, no automatic retries
on restrictions. HTTP 403/429, non-JSON, malformed envelopes and broken identities
are errors; explicit items=[] is the only empty success. No challenge bypass,
login, proxy rotation or stealth fallback was added.

Listings preserve site ID, title, finite numerical price/currency, direct item
URL, optional thumbnail/location/created-at. The synthetic fixture is clearly named
`wallapop_search_synthetic.json`: it is a regression example, not a recorded live page.

## Evidence boundary

The implementation worker reported a limited live search at 2026-10-18 13:30 UTC:
native Italian search page HTTP 200; v3 search HTTP 200; 40 normalized listings via
ordinary urllib transport. This is a worker report until independently reproduced.
The parent repeats transport/adapter validation and records it in VALIDATION.md.
This establishes response compatibility only, not a guarantee of geographic/filter
correctness, comprehensive coverage, ongoing access or contractual permission.

Private/unofficial APIs and markup can change without notice. Review marketplace
terms and use only permitted access. Stop on restrictions; do not increase load or
switch identities. Bounded polling is not evidence of platform permission.
