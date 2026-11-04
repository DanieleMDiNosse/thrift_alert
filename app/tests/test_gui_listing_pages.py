# ruff: noqa: F811
"""Reach every marketplace through real HTTP paging, never start monitoring."""

import json
import os
import pytest
from subitoo.core import db
from subitoo.core.models import Filters, Listing
from tests.test_gui import gui, request  # noqa: F401


@pytest.fixture
def catalog(gui):
    counts = {"subito": 60, "vinted": 45, "wallapop": 7}
    conn = db.init_db()
    for site, n in counts.items():
        qid = db.create_query(
            conn, name=site, site=site, search={}, filters=Filters(), cron="0 * * * *"
        )
        for i in range(n):
            db.insert_listing(
                conn,
                qid,
                Listing(
                    site_listing_id=str(i),
                    title=f"{site} item {i}",
                    price=50,
                    url=f"https://example.com/{site}/{i}",
                    raw={},
                ),
            )
    conn.execute("UPDATE listings SET first_seen_at=100,last_seen_at=100")
    conn.close()
    return counts


@pytest.mark.skipif(
    os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation"
)
def test_inline_worth_panel_works_in_real_listing_renderer(gui, catalog, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    from subitoo.ai import config
    from subitoo.ai.config import AIConfig
    from subitoo.ai.worker import Worker
    class LLM:
        def complete(self, task, context):
            assert task == 'review'
            return {'message': 'Prodotto compatto, ma poco versatile. Adatto agli usi semplici.'}

    cfg = AIConfig(_env_file=None, workflow="on_demand", provider="openai", api_key="synthetic-test")
    monkeypatch.setattr(config, "get_config", lambda: cfg)

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{gui.server_port}")
        link = page.get_by_role("link", name="subito item 59", exact=True)
        row = link.locator("..").locator("..")
        with page.expect_response('**/api/ai/review') as pending:
            row.get_by_role("button", name="Recensisci", exact=True).click()
        assert pending.value.status == 200
        conn = db.init_db()
        assert Worker(conn, config=cfg, llm=LLM()).run_once()
        conn.close()
        expect(row.locator(".worth-message")).to_contain_text("Prodotto compatto", timeout=7000)
        expect(row.locator(".worth-new-price")).to_have_count(0)
        expect(page.locator("#ai-assistant")).to_be_hidden()
        browser.close()


def test_balanced_page_has_all_sites_and_reaches_entire_archive(gui, catalog):
    seen = []
    total = sum(catalog.values())
    for offset in range(0, total, 40):
        code, body, _ = request(gui, f"/api/state?listing_offset={offset}")
        assert code == 200
        data = json.loads(body)
        assert data["listing_page"]["total"] == total
        assert data["listing_page"]["site_counts"] == catalog
        assert len(data["listings"]) <= 40
        if offset == 0:
            assert {row["site"] for row in data["listings"]} == set(catalog)
        seen.extend((row["site"], row["site_listing_id"]) for row in data["listings"])
    assert len(seen) == len(set(seen)) == total
    again = json.loads(request(gui, "/api/state?listing_offset=0")[1])
    assert seen[:40] == [(r["site"], r["site_listing_id"]) for r in again["listings"]]


def test_offset_past_current_end_recovers_to_last_page(gui, catalog):
    data = json.loads(
        request(gui, "/api/state?listing_site=wallapop&listing_offset=80")[1]
    )
    assert data["listing_page"]["offset"] == 0
    assert len(data["listings"]) == 7


def test_site_filter_is_applied_before_limit(gui, catalog):
    data = json.loads(request(gui, "/api/state?listing_site=vinted")[1])
    assert data["listing_page"]["total"] == 45
    assert data["listing_page"]["has_more"] is True
    assert len(data["listings"]) == 40
    assert {r["site"] for r in data["listings"]} == {"vinted"}
    last = json.loads(
        request(gui, "/api/state?listing_site=vinted&listing_offset=40")[1]
    )
    assert len(last["listings"]) == 5
    assert last["listing_page"]["has_more"] is False


@pytest.mark.parametrize(
    "query",
    [
        "listing_site=invalid",
        "listing_offset=-1",
        "listing_offset=1.5",
        "listing_site=vinted&listing_site=subito",
        "listing_view=invalid",
    ],
)
def test_listing_page_parameters_are_validated(gui, query):
    assert request(gui, "/api/state?" + query)[0] == 400


def test_listing_text_search_and_star_hide_flags(gui, catalog):
    # Star item 5 on vinted and hide item 6 on vinted.
    code, body, _ = request(gui, "/api/listings/flag", {"site": "vinted", "id": "5", "starred": True})
    assert code == 200 and json.loads(body)["starred"] is True
    code, body, _ = request(gui, "/api/listings/flag", {"site": "vinted", "id": "6", "hidden": True})
    assert code == 200 and json.loads(body)["hidden"] is True

    all_vinted = json.loads(request(gui, "/api/state?listing_site=vinted")[1])
    assert all_vinted["listing_page"]["total"] == 44  # 45 minus 1 hidden
    assert all_vinted["listing_page"]["starred_count"] == 1
    assert all_vinted["listing_page"]["hidden_count"] == 1

    starred = json.loads(request(gui, "/api/state?listing_view=starred")[1])
    assert starred["listing_page"]["total"] == 1
    assert starred["listings"][0]["site_listing_id"] == "5"
    assert starred["listings"][0]["starred"] is True

    hidden = json.loads(request(gui, "/api/state?listing_view=hidden")[1])
    assert hidden["listing_page"]["total"] == 1
    assert hidden["listings"][0]["site_listing_id"] == "6"
    assert hidden["listings"][0]["hidden"] is True

    searched = json.loads(request(gui, "/api/state?listing_q=wallapop%20item%203")[1])
    assert searched["listing_page"]["total"] == 1
    assert searched["listings"][0]["title"] == "wallapop item 3"


@pytest.mark.skipif(
    os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation"
)
def test_browser_filter_and_next_page_use_server_paging(gui, catalog):
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{gui.server_port}")
        expect(page.locator("#listing-summary")).to_contain_text("Vinted: 45")
        expect(page.locator("#listings")).to_contain_text("Wallapop")
        with page.expect_response("**/api/state?listing_site=vinted"):
            page.get_by_label("Marketplace annunci").select_option("vinted")
        expect(page.locator("#listings .listing")).to_have_count(8)
        page.get_by_role("button", name="Espandi pagina (40)", exact=True).click()
        expect(page.locator("#listings .listing")).to_have_count(40)
        page.get_by_role("button", name="Pagina successiva", exact=True).click()
        expect(page.locator("#listings .listing")).to_have_count(5)
        expect(page.locator("#listing-page-label")).to_contain_text("41–45 di 45")
        expect(page.locator("#listing-next")).to_be_disabled()
        browser.close()
