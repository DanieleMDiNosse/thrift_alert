"""Bounded Wallapop v3 search adapter. See docs/WALLAPOP.md for limitations.

No authentication, browser fallback, retries, or challenge bypass. Unknown native
filters are rejected rather than silently broadening a saved search.
"""
from __future__ import annotations

import math
import random
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from subitoo.core.fetch import FetchContext
from subitoo.core.scan import ScanListings
from subitoo.core.models import Listing, SearchField
from subitoo.registry import register
from subitoo.sites.base import BaseSite

API_URL = "https://api.wallapop.com/api/v3/search"
MAX_PAGES = 25
TIMEOUT = 120
PARAMS = {"keywords", "latitude", "longitude", "category_ids", "min_sale_price",
          "max_sale_price", "distance", "order_by", "source", "filters_source"}
ORDERS = {"newest", "price_low_to_high", "price_high_to_low"}


def _search_params(url: str) -> dict[str, str]:
    pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    params = dict(pairs)
    if len(params) != len(pairs) or set(params) - PARAMS:
        raise ValueError("wallapop: duplicate or unsupported URL parameters")
    for key, bound in (("latitude", 90), ("longitude", 180)):
        try:
            value = float(params[key])
            if not math.isfinite(value) or abs(value) > bound:
                raise ValueError
        except (KeyError, ValueError):
            raise ValueError(f"wallapop: valid {key} required in pasted search URL") from None
    for key in ("min_sale_price", "max_sale_price", "distance"):
        if key in params:
            try:
                value = float(params[key])
                if not math.isfinite(value) or value < 0:
                    raise ValueError
            except ValueError:
                raise ValueError(f"wallapop: invalid {key}") from None
    if "category_ids" in params and not re.fullmatch(r"\d+(,\d+)*", params["category_ids"]):
        raise ValueError("wallapop: invalid category_ids")
    if "order_by" in params and params["order_by"] not in ORDERS:
        raise ValueError("wallapop: unsupported order_by")
    if ("min_sale_price" in params and "max_sale_price" in params
            and float(params["min_sale_price"]) > float(params["max_sale_price"])):
        raise ValueError("wallapop: min_sale_price exceeds max_sale_price")
    params.setdefault("source", "search_box")
    params.setdefault("filters_source", "quick_filters")
    return params


def _parse_page(body: Any, root: str) -> tuple[list[Listing], str | None]:
    """Require the known envelope; an observed empty array alone means no matches."""
    try:
        items = body["data"]["section"]["payload"]["items"]
        if not isinstance(items, list):
            raise ValueError("items must be an array")
        meta = body.get("meta", {})
        cursor = meta.get("next_page")
        if cursor is not None and not isinstance(cursor, str):
            raise ValueError("next_page must be an opaque string")
        out = []
        for item in items:
            identity, title, slug = item["id"], item["title"], item["web_slug"]
            if (not isinstance(identity, (str, int)) or isinstance(identity, bool)
                    or not str(identity).strip() or not isinstance(title, str) or not title.strip()
                    or not isinstance(slug, str) or not re.fullmatch(r"[\w-]+", slug)):
                raise ValueError("invalid item identity/title/slug")
            money = item.get("price")
            amount, currency = None, "EUR"
            if money is not None:
                amount, currency = money.get("amount"), money.get("currency", "EUR")
                if ((amount is not None and (isinstance(amount, bool)
                        or not isinstance(amount, (float, int))
                        or not math.isfinite(amount) or amount < 0))
                        or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
                    raise ValueError("invalid price")
            images = item.get("images")
            images = [] if images is None else images
            if not isinstance(images, list):
                raise ValueError("images must be an array")
            image = (images[0].get("urls") or {}).get("big") if images else None
            if image is not None and (not isinstance(image, str)
                                      or urlsplit(image).scheme not in {"https", "http"}
                                      or not urlsplit(image).netloc):
                raise ValueError("invalid image URL")
            location = item.get("location")
            location = {} if location is None else location
            city = location.get("city")
            if city is not None and not isinstance(city, str):
                raise ValueError("invalid location city")
            stamp = item.get("created_at")
            if stamp is not None and (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                                      or not math.isfinite(stamp) or stamp < 0):
                raise ValueError("invalid created_at milliseconds")
            out.append(Listing(
                site_listing_id=str(identity), title=title,
                price=float(amount) if amount is not None else None, currency=currency,
                url=f"{root}/item/{slug}", shipping_available=None, location=city,
                image_url=image, posted_at=int(stamp / 1000) if stamp is not None else None,
                raw=item,
            ))
        return out, cursor
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError) as exc:
        raise RuntimeError(f"wallapop: malformed search response: {exc}") from exc


@register("wallapop")
class WallapopSite(BaseSite):
    needs_browser = False
    url_host_pattern = r"^(?:(?:www|it|es|en)\.)?wallapop\.com$"
    search_schema = [
        SearchField("url", "Wallapop /app/search URL (including latitude/longitude)"),
        SearchField("max_pages", "Max pages (1–25)", required=False, default="1"),
    ]

    def validate_search(self, search: dict[str, Any]) -> dict[str, Any]:
        search = super().validate_search(dict(search))
        parts = urlsplit(search["url"])
        if (parts.scheme != "https" or parts.username or parts.password or parts.port
                or parts.path.rstrip("/") != "/app/search" or parts.fragment):
            raise ValueError("wallapop: expected an HTTPS /app/search URL without credentials, port or fragment")
        _search_params(search["url"])
        pages = search.get("max_pages", "1")
        if (isinstance(pages, bool) or not re.fullmatch(r"[0-9]+", str(pages))
                or not 1 <= int(pages) <= MAX_PAGES):
            raise ValueError("wallapop: max_pages must be an integer from 1 to 25")
        search["max_pages"] = str(pages)
        return search

    def fetch(self, search: dict[str, Any], ctx: FetchContext) -> ScanListings:
        search = self.validate_search(search)
        parts = urlsplit(search["url"])
        root = f"https://{parts.netloc}"
        params = _search_params(search["url"])
        headers = {"Accept": "application/json", "X-DeviceOS": "0",
                   "Origin": root, "Referer": root + "/"}
        out = ScanListings()
        seen_cursors: set[str] = set()
        for page_no in range(int(search["max_pages"])):
            if page_no:
                time.sleep(random.uniform(2.5, 5.0))
            try:
                response = ctx.http.get(API_URL + "?" + urlencode(params), headers=headers,
                                        timeout=TIMEOUT, allow_redirects=False)
            except Exception as exc:
                raise RuntimeError(f"wallapop: request failed ({type(exc).__name__})") from exc
            if response.status_code != 200:
                raise RuntimeError(f"wallapop: search failed: HTTP {response.status_code}; no retry or bypass attempted")
            try:
                body = response.json()
            except ValueError as exc:
                raise RuntimeError("wallapop: malformed search response (not JSON)") from exc
            listings, cursor = _parse_page(body, root)
            out.extend(listings)
            out.pages += 1
            stop_page = getattr(ctx, "stop_page", None)
            if callable(stop_page) and stop_page(listings):
                out.complete, out.reason = True, "end"
                break
            if not listings or not cursor:
                out.complete, out.reason = True, "end"
                break
            if cursor in seen_cursors:
                out.reason = "repeated_cursor"
                break
            seen_cursors.add(cursor)
            params["next_page"] = cursor
        return out
