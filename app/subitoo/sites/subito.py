"""Subito.it site adapter.

Strategy (SPEC.md Decision 19): Subito only serves its results to a real browser, so we
fetch *entirely through Camoufox* (`needs_browser = True`) — no HTTP/cookie handoff. The user fine-tunes
the search on Subito itself and pastes the resulting page URL; we let Subito build its
own hades `/search/items` API call for us. Two phases, both site-specific and both living
in THIS file (the core only lends a generic page handle — see `core/fetch.py`):

  add-time  `resolve()` -> `_capture_api_url()`: load the pasted page, dismiss the cookie
            wall, toggle "Cerca solo nel titolo" to make Subito fire `/search/items`,
            capture that URL, normalize it, and store it in the search blob.
  run-time  `fetch()`: warm one session on the page URL (establishes the browser session), then
            replay the stored API URL per page (`start += 30`), parsing each JSON body.

A block or challenge shows up as a non-JSON body, which `fetch()` surfaces as a clear error;
the run then errors and retries on the next schedule (re-warming from scratch).

The API endpoint + JSON shape are the parts the SPEC marks as needing live iteration.
Parsing is kept in module-level pure functions so it can be unit-tested against recorded
fixtures with no network or browser (the adapter contract harness).
"""

from __future__ import annotations

import json
import math
import random
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from subitoo.core.fetch import FetchContext
from subitoo.core.scan import ScanListings
from subitoo.core.models import Listing, SearchField
from subitoo.sites.base import BaseSite
from subitoo.registry import register

PAGE_SIZE = 30  # Subito serves 30 listings/page; we mirror it exactly (stay low).

#: Didomi cookie wall — real users must clear it before the UI is interactive.
CONSENT_SELECTORS = (
    "text=Continua senza accettare",
    "#didomi-notice-disagree-button",
    "button:has-text('Continua senza accettare')",
)
#: Toggling "Cerca solo nel titolo" forces the client-side search we capture.
TRIGGER_SELECTORS = (
    "text=Cerca solo nel titolo",
    "label:has-text('Cerca solo nel titolo')",
    "text=solo nel titolo",
)


def _with_start(api_url: str, start: int) -> str:
    """Return the resolved API URL with its pagination offset set to ``start``."""
    parts = urlsplit(api_url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["start"] = str(start)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _with_query_from_page(api_url: str, page_url: str) -> str | None:
    """Swap the search keyword ``q`` on an already-captured Hades API URL when
    iterating over multiple phrases of the same multi-phrase search."""
    page_q = dict(parse_qsl(urlsplit(page_url).query, keep_blank_values=True)).get("q")
    if not page_q:
        return None
    parts = urlsplit(api_url)
    pairs = [(k, page_q if k == "q" else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    if not any(k == "q" for k, _ in pairs):
        pairs.insert(0, ("q", page_q))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _with_prices(api_url: str, search: dict[str, Any]) -> str:
    """Override only explicit price bounds; retain captured native URL filters."""
    parts = urlsplit(api_url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    overrides = {}
    for key, native in (("price_min", "ps"), ("price_max", "pe")):
        value = search.get(key)
        if value is None:
            continue
        try:
            number = float(value)
            if isinstance(value, bool) or not math.isfinite(number) or number < 0:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f"subito: invalid {key} price") from None
        overrides[native] = format(number, ".15g")
    effective = dict(pairs) | overrides
    if "ps" in effective and "pe" in effective:
        try:
            if float(effective["ps"]) > float(effective["pe"]):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("subito: price_min exceeds price_max or invalid price range") from None
    if not overrides:
        return api_url
    query = [(k, v) for k, v in pairs if k not in overrides] + list(overrides.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _is_search_items(url: str) -> bool:
    """The real results endpoint — not the promoted `/gallerized` or `/geoboosted`."""
    return "hades.subito.it" in url and "/search/items?" in url


def _read_intended_qso(page) -> bool:
    """Read the page's own resolved `qso` before we toggle it to trigger the search."""
    try:
        txt = page.eval_js(
            "() => {const e=document.getElementById('__NEXT_DATA__'); return e ? e.textContent : null;}"
        )
        data = json.loads(txt)
        return bool(data["props"]["pageProps"]["initialState"]["search"]["qso"])
    except Exception:
        return False


def _normalize_api_url(url: str, *, qso: bool, lim: int = PAGE_SIZE) -> str:
    """Fix only the knobs we own on Subito's captured URL: restore the page's `qso`
    (the trigger toggle flips it) and reset pagination to the first page."""
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["qso"] = "true" if qso else "false"
    q["start"] = "0"
    q["lim"] = str(lim)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _capture_api_url(page_url: str, ctx: FetchContext) -> str:
    """Let Subito build its own hades `/search/items` URL and capture it.

    Loads the pasted page, dismisses the cookie wall, toggles "Cerca solo nel titolo"
    to make Subito fire the client-side search, and grabs the request URL — normalized
    with the page's own `qso` and reset to the first page. All the Subito-specific
    browser choreography lives here; the core only lends a generic page handle.
    """
    with ctx.browser.session() as page:
        page.goto(page_url, wait=4)
        intended_qso = _read_intended_qso(page)
        page.click_first(CONSENT_SELECTORS)
        page.wait(2)
        page.click_first(TRIGGER_SELECTORS, scroll=True)
        page.wait(6)
        hits = [u for u in page.requests if _is_search_items(u)]
    if not hits:
        raise RuntimeError(
            "subito: could not capture the search API URL — the page layout or "
            "consent/toggle controls may have changed. Paste the page URL and retry."
        )
    return _normalize_api_url(hits[-1], qso=intended_qso)


def _extract_price(ad: dict[str, Any]) -> float | None:
    """Subito puts price inside the `features` list under a `/price` uri."""
    for feat in ad.get("features", []):
        if feat.get("uri", "").endswith("/price"):
            for val in feat.get("values", []):
                raw = val.get("key") or val.get("value")
                if raw is None:
                    continue
                try:
                    return float(str(raw).replace(",", "."))
                except ValueError:
                    return None
    return None


def _extract_shipping(ad: dict[str, Any]) -> bool | None:
    """`/item_shippable` is the one shipping flag Subito puts on every ad (1=Sì / 0=No).
    The other /item_shipping_* features are metadata (cost, carriers, type, size)."""
    for feat in ad.get("features", []):
        uri = feat.get("uri", "")
        if uri.endswith("/item_shippable") or uri.endswith("/shipping"):
            for val in feat.get("values", []):
                # `key` is the language-neutral code ("1"/"0"); `value` is the
                # localized label ("Sì"/"No"). Trust the key, not the label.
                v = str(val.get("key") or val.get("value") or "").strip().lower()
                if v in ("1", "true", "yes", "si", "sì"):
                    return True
                if v in ("0", "false", "no"):
                    return False
    return None


# Subito renders images on demand; the API gives us a ready path in `cdn_base_url`
# and we pick a render rule. `uri` is only an internal handle (imgid:<uuid>), not a URL.
_IMG_RULE = "gallery-desktop-1x-auto"


def _extract_image(ad: dict[str, Any]) -> str | None:
    images = ad.get("images") or []
    if not images or not isinstance(images[0], dict):
        return None
    base = images[0].get("cdn_base_url")
    return f"{base}?rule={_IMG_RULE}" if base else None


def _extract_location(ad: dict[str, Any]) -> str | None:
    """Most specific place name available (comune > provincia > regione), suffixed with
    the province abbreviation, e.g. "Primiero San Martino di Castrozza (TN)". The
    `short_name` ("TN") lives on the `city` node — which Subito labels "Provincia"."""
    geo = ad.get("geo") or {}
    name = None
    for key in ("town", "city", "region"):
        node = geo.get(key)
        if isinstance(node, dict) and node.get("value"):
            name = node["value"]
            break
    if not name:
        return None
    city = geo.get("city")
    short = city.get("short_name") if isinstance(city, dict) else None
    return f"{name} ({short})" if short else name


def _extract_posted_at(ad: dict[str, Any]) -> int | None:
    """Subito's publish time -> epoch seconds. We read `display_iso8601` (e.g.
    "2026-07-07T11:26:24+0200") because it carries the Europe/Rome offset; the plain
    `display` string is naive. Stored as epoch to match first/last_seen_at."""
    iso = (ad.get("dates") or {}).get("display_iso8601")
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return None


def parse_item(ad: dict[str, Any]) -> Listing:
    urls = ad.get("urls") or {}
    return Listing(
        site_listing_id=str(ad.get("urn") or ad.get("item_id") or ad.get("id") or ""),
        title=ad.get("subject") or ad.get("title") or "",
        price=_extract_price(ad),
        currency="EUR",
        url=urls.get("default") or ad.get("url") or "",
        shipping_available=_extract_shipping(ad),
        location=_extract_location(ad),
        image_url=_extract_image(ad),
        posted_at=_extract_posted_at(ad),
        raw=ad,
    )


def parse_items(payload: dict[str, Any]) -> list[Listing]:
    if not isinstance(payload, dict):
        raise RuntimeError("subito: expected search object")
    ads = payload.get("ads") if "ads" in payload else payload.get("results")
    if not isinstance(ads, list) or any(not isinstance(ad, dict) for ad in ads):
        raise RuntimeError("subito: missing or malformed search items")
    items = [parse_item(ad) for ad in ads]
    if any(not x.site_listing_id or not x.title or not x.url for x in items):
        raise RuntimeError("subito: invalid listing identity/title/link")
    return items


@register("subito")
class SubitoSite(BaseSite):
    needs_browser = True

    #: The user fine-tunes the search on Subito itself and pastes the resulting page
    #: URL; we let Subito build the API call (see ``resolve``). Everything Subito can
    #: express (region, nearby, category, sort, keyword) lives in that URL.
    #: Explicit search price bounds also override ps/pe on the captured API URL;
    #: local universal filters remain a final check.
    search_schema = [
        SearchField("url", "Subito search URL (paste from your browser's address bar)"),
        SearchField("max_pages", "Max pages to fetch (30 listings each)",
                    required=False, default="1"),
    ]

    #: The base class uses this both to claim a pasted URL (so `query add` doesn't
    #: have to ask which site) and to reject a URL from anywhere else.
    url_host_pattern = r"(^|\.)subito\.it$"

    def validate_search(self, search: dict[str, Any]) -> dict[str, Any]:
        super().validate_search(search)  # required fields + the subito.it URL check
        mp = search.get("max_pages", "1")
        if (isinstance(mp, bool) or not isinstance(mp, (str, int))
                or not str(mp).isdecimal() or not 1 <= int(mp) <= 30):
            raise ValueError("subito: 'max_pages' must be an integer from 1 to 30")
        search["max_pages"] = str(int(mp))
        return search

    def resolve(self, search: dict[str, Any], ctx: FetchContext) -> dict[str, Any]:
        # Let Subito build the hades URL: load the pasted page, capture the
        # /search/items call it fires, and store it for fast runtime replay.
        # Skipped if the blob already carries one (a `--from-json` import of an
        # already-resolved query) — that's why bulk imports don't fire a browser
        # session per query. Drop `api_url` from the blob to force a re-capture.
        if not search.get("api_url"):
            search["api_url"] = _capture_api_url(search["url"], ctx)
        return search

    def fetch(self, search: dict[str, Any], ctx: FetchContext) -> ScanListings:
        search = self.validate_search(dict(search))
        _with_prices("", search)  # reject invalid explicit bounds before browser work
        page_url = search["url"]
        # Re-resolve on the fly if the query was created without a resolve step
        # (e.g. --from-json), or reuse the captured Hades API template across phrases.
        raw_api_url = search.get("api_url")
        if not raw_api_url:
            cached_template = getattr(ctx, "_subito_api_url", None)
            if isinstance(cached_template, str):
                raw_api_url = _with_query_from_page(cached_template, page_url)
        if not raw_api_url:
            raw_api_url = _capture_api_url(page_url, ctx)
        try:
            ctx._subito_api_url = raw_api_url
        except AttributeError:
            pass
        api_url = _with_prices(raw_api_url, search)
        max_pages = int(search["max_pages"])

        out = ScanListings()
        # One warmed browsing session, paged like a human — warm once, not per page.
        with ctx.browser.session(warm_url=page_url) as page:
            for page_no in range(max_pages):
                page.goto(_with_start(api_url, page_no * PAGE_SIZE))
                body = page.text()
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError as e:
                    snippet = body[:200].replace("\n", " ")
                    raise RuntimeError(
                        f"subito: expected JSON but got non-JSON (a block/challenge or the "
                        f"resolved URL went stale?): {snippet!r}"
                    ) from e
                items = parse_items(payload)
                out.extend(items)
                out.pages += 1
                stop_page = getattr(ctx, "stop_page", None)
                if callable(stop_page) and stop_page(items):
                    out.complete, out.reason = True, "end"
                    break
                if len(items) < PAGE_SIZE:
                    out.complete, out.reason = True, "end"
                    break  # only an observed short page establishes the end
                if page_no < max_pages - 1:
                    time.sleep(random.uniform(2.5, 5.0))  # stay low: human pacing
        return out
