"""Offline Wallapop contract; fixture is synthetic, never claimed live-captured."""
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from subitoo.registry import get_site, site_for_url
from subitoo.sites import wallapop

URL = "https://it.wallapop.com/app/search?keywords=camera&latitude=41.9&longitude=12.5&min_sale_price=0&max_sale_price=200&order_by=newest"


@pytest.fixture
def payload():
    return json.loads((Path(__file__).parent / "fixtures/wallapop_search_synthetic.json").read_text())


class Http:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        status, body = value
        def decode():
            if isinstance(body, Exception):
                raise body
            return body
        return SimpleNamespace(status_code=status, json=decode)


def fetch(http, **search):
    return list(wallapop.WallapopSite().fetch({"url": URL, **search}, SimpleNamespace(http=http)))


def test_registry_and_url_validation():
    assert isinstance(get_site("wallapop"), wallapop.WallapopSite)
    assert site_for_url(URL) == "wallapop"
    normalized = wallapop.WallapopSite().validate_search({"url": "  " + URL + "  "})
    assert normalized == {"url": URL, "max_pages": "1"}
    assert not wallapop.WallapopSite.needs_browser


@pytest.mark.parametrize("url", [
    URL.replace("https:", "http:"), URL.replace("it.wallapop.com", "it.wallapop.com.evil.test"),
    URL.replace("it.wallapop.com", "evil@it.wallapop.com"), URL.replace("it.wallapop.com", "it.wallapop.com:443"),
    URL.replace("/app/search", "/item/camera"), URL + "&time_filter=today", URL + "&next_page=old",
    URL + "&latitude=42", URL.replace("latitude=41.9", "latitude=nan"),
    URL.replace("longitude=12.5", "longitude=181"), URL.replace("latitude=41.9&", ""),
    URL + "&shipping=true", URL + "#ignored", URL.replace("order_by=newest", "order_by=made_up"),
    URL.replace("order_by=newest", "order_by=closest"),
])
def test_rejects_unsafe_or_unsupported_search(url):
    with pytest.raises(ValueError, match="wallapop"):
        wallapop.WallapopSite().validate_search({"url": url})


@pytest.mark.parametrize("pages", [0, -1, 26, True, 1.5, "1.5", "no"])
def test_page_bound_validated_before_network(pages):
    http = Http()
    with pytest.raises(ValueError, match="max_pages"):
        fetch(http, max_pages=pages)
    assert http.calls == []


def test_fixture_normalizes_and_bounded_request(payload):
    http = Http((200, payload))
    first, second = fetch(http)
    assert (first.site_listing_id, first.title, first.price, first.currency) == ("synthetic-1", "Synthetic camera", 125.5, "EUR")
    assert first.url == "https://it.wallapop.com/item/synthetic-camera-1"
    assert first.location == "Roma"
    assert first.posted_at == 1700000000
    assert first.image_url == "https://example.invalid/camera.jpg"
    assert first.shipping_available is None  # loose reference field is not a verified contract
    assert first.raw == payload["data"]["section"]["payload"]["items"][0]
    assert second.price == 0 and second.image_url is None and second.posted_at is None
    assert len(http.calls) == 1
    url, options = http.calls[0]
    assert urlsplit(url).netloc == "api.wallapop.com"
    params = parse_qs(urlsplit(url).query)
    assert params["keywords"] == ["camera"]
    assert params["min_sale_price"] == ["0"]
    assert params["source"] == ["search_box"]
    assert params["filters_source"] == ["quick_filters"]
    assert options["timeout"] == 120
    assert options["allow_redirects"] is False


def test_opaque_cursor_and_page_limit(payload):
    http = Http((200, payload), (200, payload))
    assert len(fetch(http, max_pages="2")) == 4
    assert len(http.calls) == 2
    assert parse_qs(urlsplit(http.calls[1][0]).query)["next_page"] == ["opaque+/=cursor"]


@pytest.mark.parametrize("meta", [{}, {"next_page": None}, {"next_page": ""}])
def test_stops_at_last_page(payload, meta):
    payload["meta"] = meta
    http = Http((200, payload))
    assert len(fetch(http, max_pages=5)) == 2
    assert len(http.calls) == 1


def test_valid_empty_stops_even_with_cursor(payload):
    payload["data"]["section"]["payload"]["items"] = []
    http = Http((200, payload))
    assert fetch(http, max_pages=5) == []
    assert len(http.calls) == 1


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500])
def test_http_failure_never_empty_or_retried(status):
    http = Http((status, {}))
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        fetch(http)
    assert len(http.calls) == 1


@pytest.mark.parametrize("body", [None, [], {}, {"data": {}}, {"data": {"section": {"payload": {"items": None}}}}, ValueError("HTML challenge")])
def test_malformed_body_fails_explicitly(body):
    with pytest.raises(RuntimeError, match="wallapop.*malformed"):
        fetch(Http((200, body)))


@pytest.mark.parametrize("patch", [
    {"id": None}, {"id": True}, {"title": ""}, {"web_slug": "https://evil.test"},
    {"price": {"amount": "bad", "currency": "EUR"}},
    {"price": {"amount": float("nan"), "currency": "EUR"}},
    {"price": {"amount": True, "currency": "EUR"}},
    {"images": "wrong"}, {"created_at": "yesterday"},
    {"images": [{"urls": {"big": 123}}]}, {"location": {"city": ["Roma"]}},
    {"images": [{"urls": {"big": "javascript:alert(1)"}}]},
])
def test_malformed_item_fails_instead_of_skipping(payload, patch):
    payload["data"]["section"]["payload"]["items"][0].update(patch)
    with pytest.raises(RuntimeError, match="wallapop.*malformed"):
        fetch(Http((200, payload)))


@pytest.mark.parametrize("optional", [{"price": None}, {"images": None},
                                     {"images": [{"urls": {}}]}, {"location": None}])
def test_missing_optional_fields_are_safe(payload, optional):
    item = payload["data"]["section"]["payload"]["items"][0]
    item.update(optional)
    result = fetch(Http((200, payload)))
    assert len(result) == 2
    if "price" in optional:
        assert result[0].price is None
    if "images" in optional:
        assert result[0].image_url is None


def test_malformed_cursor_fails(payload):
    payload["meta"]["next_page"] = {"url": "https://evil.test"}
    with pytest.raises(RuntimeError, match="wallapop.*malformed"):
        fetch(Http((200, payload)))


def test_transport_failure_is_not_empty():
    with pytest.raises(RuntimeError, match="wallapop.*request failed"):
        fetch(Http(TimeoutError("timeout")))


def test_second_page_failure_does_not_return_partial_results(payload):
    with pytest.raises(RuntimeError, match="HTTP 429"):
        fetch(Http((200, payload), (429, {})), max_pages=2)
