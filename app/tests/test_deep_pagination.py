"""Offline coverage contracts for bounded native pagination (synthetic pages)."""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from subitoo.sites import subito, vinted, wallapop

URLS = {
    "subito": "https://www.subito.it/annunci-italia/vendita/usato/?q=camera",
    "vinted": "https://www.vinted.it/catalog?search_text=camera&status_ids[]=1&status_ids[]=2",
    "wallapop": "https://it.wallapop.com/app/search?keywords=camera&latitude=41&longitude=12",
}
API = "https://hades.subito.it/v1/search/items?q=camera&qso=true&ps=5&pe=900&category=0"


class Browser:
    def __init__(self, bodies):
        self.bodies = iter(bodies)
        self.urls = []

    @contextmanager
    def session(self, **kwargs):
        yield self

    def goto(self, url):
        self.urls.append(url)

    def text(self):
        value = next(self.bodies)
        return json.dumps(value)


class Http:
    def __init__(self, bodies):
        self.bodies = iter(bodies)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        # Vinted's warm-up does not consume a results page.
        if url == "https://www.vinted.it/":
            return SimpleNamespace(status_code=200)
        body = next(self.bodies)
        if isinstance(body, Exception):
            raise body
        return SimpleNamespace(status_code=200, json=lambda: body, headers={}, text=body)


def body(site, count, *, page=1, cursor="next", total_pages=3, total_entries=193):
    if site == "subito":
        return {"ads": [{"id": f"{page}-{i}", "subject": "Camera", "url": "https://www.subito.it/a.htm"} for i in range(count)]}
    if site == "wallapop":
        return {"data": {"section": {"payload": {"items": [
            {"id": f"{page}-{i}", "title": "Camera", "web_slug": "camera"} for i in range(count)]}}}, "meta": {"next_page": cursor}}
    data = {"items": {"items": [{"productItem": {"id": f"{page}-{i}", "title": "Camera", "url": "/items/camera"}} for i in range(count)]},
            "pagination": {"current_page": page, "total_pages": total_pages, "per_page": 96, "total_entries": total_entries, "time": 123}}
    return json.dumps(data, separators=(",", ":"))


def run(site, bodies, **search):
    browser, http = Browser(bodies if site == "subito" else []), Http(bodies if site != "subito" else [])
    cls = {"subito": subito.SubitoSite, "vinted": vinted.VintedSite, "wallapop": wallapop.WallapopSite}[site]
    result = cls().fetch({"url": URLS[site], **({"api_url": API} if site == "subito" else {}), **search}, SimpleNamespace(browser=browser, http=http))
    return result, browser.urls if site == "subito" else [u for u in http.urls if u != "https://www.vinted.it/"]


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    # All three adapters share the stdlib time module.
    import time
    waits = []
    monkeypatch.setattr(time, "sleep", waits.append)
    return waits


def test_scan_list_contract():
    from subitoo.core.scan import ScanListings
    result = ScanListings([1, 2], pages=3, complete=True, reason="end")
    assert isinstance(result, list) and result == [1, 2]
    assert result.coverage == {"pages": 3, "complete": True, "reason": "end"}
    assert ScanListings().coverage == {"pages": 0, "complete": False, "reason": "page_limit"}


@pytest.mark.parametrize("site,size", [("subito", 30), ("vinted", 96), ("wallapop", 2)])
def test_multi_page_native_end_and_pacing(site, size, no_pacing):
    pages = [body(site, size, page=1), body(site, size, page=2, cursor="last"), body(site, 1, page=3, cursor=None)]
    result, urls = run(site, pages, max_pages=5)
    assert result.coverage == {"pages": 3, "complete": True, "reason": "end"}
    assert len(result) == size * 2 + 1 and len(urls) == 3
    assert len(no_pacing) == 2 and all(2.5 <= value <= 5 for value in no_pacing)
    params = [parse_qs(urlsplit(u).query) for u in urls]
    if site == "subito":
        assert [p["start"] for p in params] == [["0"], ["30"], ["60"]]
    elif site == "vinted":
        assert [p["page"] for p in params] == [["1"], ["2"], ["3"]]
        assert params[1]["time"] == ["123"]
        assert params[1]["status_ids[]"] == ["1", "2"]
    else:
        assert params[1]["next_page"] == ["next"] and params[2]["next_page"] == ["last"]


@pytest.mark.parametrize("site,size", [("subito", 30), ("vinted", 96), ("wallapop", 2)])
def test_page_limit_is_not_complete(site, size):
    result, urls = run(site, [body(site, size)], max_pages=1)
    assert result.coverage == {"pages": 1, "complete": False, "reason": "page_limit"}
    assert len(urls) == 1


@pytest.mark.parametrize("site", URLS)
def test_empty_is_observed_end(site):
    result, _ = run(site, [body(site, 0, total_pages=1, total_entries=0)], max_pages=5)
    assert result == [] and result.coverage == {"pages": 1, "complete": True, "reason": "end"}


@pytest.mark.parametrize("site,bound", [("subito", 30), ("vinted", 10), ("wallapop", 25)])
def test_expanded_bounds_and_raw_fetch_validation(site, bound):
    cls = {"subito": subito.SubitoSite, "vinted": vinted.VintedSite, "wallapop": wallapop.WallapopSite}[site]
    assert cls().validate_search({"url": URLS[site], "max_pages": bound})["max_pages"] == str(bound)
    for value in [bound + 1, 0, -1, True, 1.5, "no"]:
        with pytest.raises(ValueError, match="max_pages"):
            run(site, [], max_pages=value)


def test_wallapop_repeated_cursor_is_incomplete():
    result, urls = run("wallapop", [body("wallapop", 2), body("wallapop", 2, page=2)], max_pages=25)
    assert result.coverage == {"pages": 2, "complete": False, "reason": "repeated_cursor"}
    assert len(urls) == 2


def test_wallapop_late_failure_does_not_return_partial():
    with pytest.raises(RuntimeError, match="request failed"):
        run("wallapop", [body("wallapop", 2), TimeoutError()], max_pages=25)


@pytest.mark.parametrize("entries,complete,reason", [(2000, False, "site_limit"), (960, True, "end")])
def test_vinted_site_cap_is_not_invented_exhaustiveness(entries, complete, reason):
    result, urls = run("vinted", [body("vinted", 96, page=i, total_pages=10, total_entries=entries) for i in range(1, 11)], max_pages=10)
    assert result.coverage == {"pages": 10, "complete": complete, "reason": reason}
    assert len(urls) == 10


def test_vinted_missing_pagination_does_not_invent_end():
    page = json.loads(body("vinted", 96))
    del page["pagination"]
    result, urls = run("vinted", [json.dumps(page, separators=(",", ":"))] * 2, max_pages=2)
    assert result.coverage == {"pages": 2, "complete": False, "reason": "page_limit"}
    assert len(urls) == 2


@pytest.mark.parametrize("prices,expected", [({}, {"ps": ["5"], "pe": ["900"]}), ({"price_min": 0, "price_max": 75.5}, {"ps": ["0"], "pe": ["75.5"]}), ({"price_max": 100}, {"ps": ["5"], "pe": ["100"]})])
def test_subito_native_prices_preserve_other_filters(prices, expected):
    _, urls = run("subito", [body("subito", 30), body("subito", 0)], max_pages=2, **prices)
    for url in urls:
        params = parse_qs(urlsplit(url).query)
        assert {key: params[key] for key in expected} == expected
        assert params["category"] == ["0"] and params["qso"] == ["true"]


@pytest.mark.parametrize("prices", [{"price_min": -1}, {"price_max": float("inf")}, {"price_min": float("nan")}, {"price_min": 10, "price_max": 2}, {"price_max": "bad"}, {"price_min": True}])
def test_subito_rejects_unsafe_price_before_network(prices):
    with pytest.raises(ValueError, match="price"):
        run("subito", [], **prices)


def test_subito_full_page_with_native_total_is_not_end():
    page = body("subito", 30)
    page["total"] = 30
    result, _ = run("subito", [page], max_pages=1)
    assert result.coverage == {"pages": 1, "complete": False, "reason": "page_limit"}


@pytest.mark.parametrize("site,bound,size", [("subito", 30, 30), ("vinted", 10, 96), ("wallapop", 25, 2)])
def test_full_configured_bound_is_exercised(site, bound, size):
    pages = [body(site, size, page=i, cursor=f"cursor-{i}", total_pages=10, total_entries=2000)
             for i in range(1, bound + 1)]
    result, urls = run(site, pages, max_pages=bound)
    assert len(urls) == bound and result.pages == bound
    assert not result.complete
    assert result.reason == ("site_limit" if site == "vinted" else "page_limit")


@pytest.mark.parametrize("count", [0, 1])
@pytest.mark.parametrize("max_pages", [1, 3])
def test_vinted_short_page_respects_advertised_remaining_pages(count, max_pages):
    pages = [body("vinted", count, page=i) for i in range(1, 3)]
    pages.append(body("vinted", 1, page=3))
    result, urls = run("vinted", pages, max_pages=max_pages)
    assert len(urls) == max_pages
    assert result.coverage == {"pages": max_pages, "complete": max_pages == 3,
                               "reason": "end" if max_pages == 3 else "page_limit"}


def test_vinted_full_native_last_page_is_complete():
    result, urls = run("vinted", [body("vinted", 96, total_pages=1, total_entries=96)], max_pages=10)
    assert result.coverage == {"pages": 1, "complete": True, "reason": "end"}
    assert len(urls) == 1


def test_vinted_short_site_capped_page_cannot_claim_all_entries():
    pages = [body("vinted", 96 if i < 10 else 95, page=i, total_pages=10, total_entries=2000)
             for i in range(1, 11)]
    result, _ = run("vinted", pages, max_pages=10)
    assert result.coverage == {"pages": 10, "complete": False, "reason": "site_limit"}


@pytest.mark.parametrize("site", URLS)
def test_late_malformed_page_never_returns_partial(site):
    first = body(site, 96 if site == "vinted" else 30)
    with pytest.raises(RuntimeError):
        run(site, [first, {} if site != "vinted" else "bad"], max_pages=2)
