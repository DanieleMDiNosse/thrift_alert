# ruff: noqa: F811
"""Saved-search filtering precedes deduplication and paging, including overlaps."""

import json
import os
import pytest
from subitoo.core import db, watches
from subitoo.core.searches import build_watch
from subitoo.core.models import Listing
from tests.test_gui import gui, request  # noqa: F401


@pytest.fixture
def searches(gui):
    conn = db.init_db()
    result = {}
    for name in ("alpha", "beta"):
        wid = watches.save(conn, build_watch({"query": name, "enabled": False}))
        queries = {q.site: q.id for q in watches.queries(conn, wid)}
        keys = set()
        for site in ("subito", "vinted", "wallapop"):
            count = 45 if name == "alpha" and site == "subito" else 1
            for i in range(count):
                native = f"{name}-{i}"
                db.insert_listing(
                    conn,
                    queries[site],
                    Listing(
                        site_listing_id=native,
                        title=f"{name} {site} {i}",
                        price=50,
                        url=f"https://example.com/{site}/{native}",
                        raw={},
                    ),
                )
                keys.add((site, native))
        db.insert_listing(
            conn,
            queries["subito"],
            Listing(
                site_listing_id="shared",
                title=f"{name} shared",
                price=50,
                url="https://example.com/shared",
                raw={},
            ),
        )
        keys.add(("subito", "shared"))
        result[name] = {"id": wid, "keys": keys}
    conn.execute("UPDATE listings SET last_seen_at=100")
    conn.execute("UPDATE listings SET last_seen_at=200 WHERE title='beta shared'")
    conn.close()
    return result


def test_watch_scope_applies_before_dedup_and_pagination(gui, searches):
    for name, watch in searches.items():
        found = []
        for offset in range(0, len(watch["keys"]), 40):
            status, body, _ = request(
                gui, f"/api/state?watch_id={watch['id']}&listing_offset={offset}"
            )
            assert status == 200
            state = json.loads(body)
            assert state["listing_page"]["total"] == len(watch["keys"])
            assert state["listing_page"]["watch_id"] == watch["id"]
            assert all(row["watch_name"] == name for row in state["listings"])
            assert all(row["title"].startswith(name) for row in state["listings"])
            found.extend(
                (row["site"], row["site_listing_id"]) for row in state["listings"]
            )
        assert set(found) == watch["keys"]
        assert len(found) == len(set(found))


def test_watch_and_marketplace_filters_combine(gui, searches):
    wid = searches["alpha"]["id"]
    all_state = json.loads(request(gui, "/api/state")[1])
    assert all_state["listing_page"]["price_distribution"] is None
    state = json.loads(
        request(gui, f"/api/state?watch_id={wid}&listing_site=vinted")[1]
    )
    assert len(state["listings"]) == 1
    assert state["listings"][0]["title"].startswith("alpha vinted")
    dist = state["listing_page"]["price_distribution"]
    assert dist is not None and dist["count"] == 1 and dist["median"] == 50.0
    assert state["listing_page"]["site_counts"]["subito"] == sum(
        site == "subito" for site, _ in searches["alpha"]["keys"]
    )
    assert len(state["watches"]) == 2


@pytest.mark.parametrize("value", ["-1", "0", "1.2", "abc", "1&watch_id=2"])
def test_watch_parameter_validation(gui, value):
    assert request(gui, "/api/state?watch_id=" + value)[0] == 400


def test_unknown_watch_does_not_return_other_search_results(gui, searches):
    state = json.loads(request(gui, "/api/state?watch_id=999999")[1])
    assert state["listing_page"]["total"] == 0
    assert state["listings"] == []


@pytest.mark.skipif(
    os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation"
)
def test_browser_search_filter_resets_page_and_preserves_site(gui, searches):
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{gui.server_port}")
        expect(page.get_by_label("Ricerca annunci", exact=True)).to_be_enabled()
        expect(page.locator("#price-distribution")).to_be_hidden()
        page.get_by_label("Ricerca annunci", exact=True).select_option(
            str(searches["alpha"]["id"])
        )
        expect(page.locator("#price-distribution")).to_be_visible()
        expect(page.locator("#price-distribution")).to_contain_text("Mediana")
        expect(page.locator("#listings")).not_to_contain_text("beta")
        page.get_by_role("button", name="Pagina successiva", exact=True).click()
        expect(page.locator("#listing-page-label")).to_contain_text("41–")
        page.get_by_label("Ricerca annunci", exact=True).select_option(
            str(searches["beta"]["id"])
        )
        expect(page.locator("#listing-page-label")).to_contain_text("1–")
        expect(page.locator("#listings")).not_to_contain_text("alpha")
        page.get_by_label("Marketplace annunci", exact=True).select_option("wallapop")
        expect(page.locator("#listings .listing")).to_have_count(1)
        expect(page.locator("#listings")).to_contain_text("beta wallapop")
        expect(
            page.get_by_role("button", name="Recensisci", exact=True)
        ).to_be_visible()
        page.get_by_label("Ricerca annunci", exact=True).select_option("")
        expect(page.locator("#listings .listing")).to_have_count(2)
        browser.close()


@pytest.mark.skipif(
    os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation"
)
def test_browser_recovers_when_selected_search_is_removed(gui, searches):
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{gui.server_port}")
        page.get_by_label("Ricerca annunci", exact=True).select_option(
            str(searches["beta"]["id"])
        )
        expect(page.locator("#listings")).to_contain_text("beta")
        assert (
            request(gui, f"/api/watches/{searches['beta']['id']}/remove", {})[0] == 200
        )
        page.get_by_label("Marketplace annunci", exact=True).select_option("vinted")
        expect(page.get_by_label("Ricerca annunci", exact=True)).to_have_value("")
        expect(page.locator("#listings")).to_contain_text("alpha vinted")
        expect(page.locator("#load-error")).to_be_hidden()
        browser.close()
