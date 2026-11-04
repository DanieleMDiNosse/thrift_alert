# ruff: noqa: F811
"""Price order covers the archive, respects filters and keeps unknowns last."""

import json
import os
import pytest
from subitoo.core import db
from tests.test_gui import gui, request  # noqa: F401
from tests.test_gui_listing_pages import catalog  # noqa: F401
from tests.test_gui_watch_filter import searches  # noqa: F401


def test_sort_combines_watch_site_and_page_parameters(gui, searches):
    wid = searches["alpha"]["id"]
    code, body, _ = request(
        gui,
        f"/api/state?watch_id={wid}&listing_site=subito&listing_order=price_asc&listing_offset=40",
    )
    assert code == 200
    state = json.loads(body)
    assert state["listing_page"]["order"] == "price_asc"
    assert state["listing_page"]["watch_id"] == wid
    assert state["listings"] and all(
        r["site"] == "subito" and r["watch_name"] == "alpha" for r in state["listings"]
    )


@pytest.fixture
def prices(catalog):
    conn = db.init_db()
    conn.execute("UPDATE listings SET last_price=CAST(site_listing_id AS INTEGER)+1")
    conn.execute("UPDATE listings SET last_price=NULL WHERE site_listing_id='0'")
    conn.close()


@pytest.mark.parametrize("order,reverse", [("price_asc", False), ("price_desc", True)])
def test_price_order_before_paging_and_unknown_prices_last(gui, prices, order, reverse):
    first = json.loads(request(gui, f"/api/state?listing_order={order}")[1])
    rows = []
    for offset in range(0, first["listing_page"]["total"], 40):
        code, body, _ = request(
            gui, f"/api/state?listing_order={order}&listing_offset={offset}"
        )
        assert code == 200
        data = json.loads(body)
        assert data["listing_page"]["order"] == order
        rows.extend(data["listings"])
    known = [r["price"] for r in rows if r["price"] is not None]
    assert known == sorted(known, reverse=reverse)
    assert all(r["price"] is None for r in rows[len(known) :])
    assert len({(r["site"], r["site_listing_id"]) for r in rows}) == len(rows)


def test_price_sort_with_site_filter_and_stable_ties(gui, prices):
    path = "/api/state?listing_site=vinted&listing_order=price_desc"
    data = json.loads(request(gui, path)[1])
    again = json.loads(request(gui, path)[1])
    assert all(r["site"] == "vinted" for r in data["listings"])
    assert data["listings"] == again["listings"]
    known = [r["price"] for r in data["listings"] if r["price"] is not None]
    assert known == sorted(known, reverse=True)


def test_sort_does_not_numerically_compare_different_currencies(gui, prices):
    conn = db.init_db()
    conn.execute("UPDATE listings SET currency='USD' WHERE site_listing_id='1'")
    conn.close()
    rows = json.loads(request(gui, "/api/state?listing_order=price_asc")[1])["listings"]
    groups = [r["currency"] for r in rows if r["price"] is not None]
    assert groups == sorted(groups)


def test_invalid_price_order_is_rejected(gui):
    assert request(gui, "/api/state?listing_order=untrusted")[0] == 400


@pytest.mark.skipif(
    os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation"
)
def test_browser_price_sort_resets_page_and_preserves_filters(gui, prices):
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{gui.server_port}")
        page.get_by_role("button", name="Pagina successiva", exact=True).click()
        expect(page.locator("#listing-page-label")).to_contain_text("41–")
        expect(page.get_by_label("Ordina annunci", exact=True)).to_be_visible()
        page.get_by_label("Ordina annunci", exact=True).select_option("price_desc")
        expect(page.locator("#listing-page-label")).to_contain_text("1–")
        expect(page.get_by_label("Ordina annunci", exact=True)).to_be_enabled()
        page.get_by_label("Marketplace annunci", exact=True).select_option("vinted")
        expect(page.get_by_label("Marketplace annunci", exact=True)).to_be_enabled()
        expect(page.get_by_label("Ordina annunci", exact=True)).to_have_value(
            "price_desc"
        )
        assert "listing_order=price_desc" in page.evaluate(
            'performance.getEntriesByType("resource").filter(x=>x.name.includes("/api/state?")).at(-1).name'
        )
        assert (
            page.locator("#listings .listing-price").first.inner_text().startswith("45")
        )
        browser.close()
