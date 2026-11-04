# ruff: noqa: F811
"""Real HTTP + SQLite + Firefox vertical slice; only external AI/web are fixtures."""

import json
import os

import pytest

from tests.test_gui import gui, request  # noqa: F401
from tests.test_ai_service import FakeSearch
from subitoo.ai import store
from subitoo.ai.worker import Worker
from subitoo.ai.ranking import DEFAULT_WEIGHTS
from subitoo.core import db
from subitoo.core.models import Filters, Listing

pytestmark = pytest.mark.skipif(
    os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation"
)


def test_real_gui_backend_research_comparison_rankings_and_persistent_chat(gui):
    from playwright.sync_api import sync_playwright, expect

    class Inference:
        def complete(self, task, context):
            if task == "identity":
                model = "A" if "Camera A" in context["title"] else "B"
                return {
                    "category": "camera",
                    "manufacturer": "Fixture",
                    "name": "Camera " + model,
                    "model": model,
                    "generation": None,
                    "variant": "body only",
                    "confidence": 0.9,
                }
            if task == "research":
                sid = context["sources"][0]["id"]
                return {
                    "criteria": {
                        key: {
                            "score": 80 if context["product"]["model"] == "A" else 90,
                            "source_ids": [sid],
                        }
                        for key in DEFAULT_WEIGHTS["camera"]
                    },
                    "strengths": ["Fixture-supported strength"],
                    "weaknesses": [],
                    "known_issues": [],
                    "specifications": {},
                    "summary": "Controlled test evidence, not real product advice",
                    "confidence": 0.8,
                }
            return {
                "message": "Confronto basato sulle fonti memorizzate. Costi e condizioni sono dichiarati dall’utente.",
                "source_ids": [
                    s["id"] for p in context["products"] for s in p["sources"]
                ],
            }

    conn = db.init_db()
    try:
        qid = db.create_query(
            conn,
            name="Isolated camera research",
            site="fake",
            search={},
            filters=Filters(),
            cron="*/30 * * * *",
        )
        for index in range(7):
            db.insert_listing(
                conn,
                qid,
                Listing(
                    site_listing_id=str(index),
                    title="Camera A" if index < 6 else "Camera B",
                    price=400 + index * 10,
                ),
            )
        assert request(gui, "/api/ai/settings", {"enabled": True})[0] == 200
        assert request(gui, "/api/ai/sync", {"limit": 10})[0] == 200
        worker = Worker(conn, llm=Inference(), search=FakeSearch())
        while worker.run_once():
            pass
        assert len(store.products(conn)) == 2
        for item in store.listings(conn):
            assert (
                request(
                    gui,
                    f"/api/ai/listings/{item['id']}/facts",
                    {"condition": "good", "shipping_cost": 0, "fees": 0},
                )[0]
                == 200
            )
        state = json.loads(request(gui, "/api/ai/state?category=camera")[1])
        assert len(state["rankings"]["quality"]) == 2
        assert state["rankings"]["quality"][0]["name"] == "Camera B"
        assert state["rankings"]["value"] and state["rankings"]["deal"]
        with sync_playwright() as browser_api:
            browser = browser_api.firefox.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{gui.server_port}")
            page.get_by_label("Seleziona Camera A").check()
            page.get_by_label("Seleziona Camera B").check()
            page.get_by_role("button", name="Confronta selezionati").click()
            expect(
                page.get_by_role("region", name="Confronto prodotti").get_by_role(
                    "table"
                )
            ).to_be_visible()
            page.get_by_role("button", name="Nuova conversazione").click()
            expect(
                page.get_by_role("button", name="Svuota conversazione")
            ).to_be_enabled()
            page.get_by_label("Messaggio", exact=True).fill("Quale consigli e perché?")
            with page.expect_response("**/messages"):
                page.get_by_role("button", name="Invia messaggio").click()
            assert worker.run_once()
            expect(page.get_by_role("log")).to_contain_text(
                "Confronto basato sulle fonti memorizzate", timeout=10000
            )
            expect(page.get_by_role("log").get_by_role("link").first).to_have_attribute(
                "href", "https://example.com/review"
            )
            page.reload()
            expect(page.get_by_role("log")).to_contain_text(
                "Confronto basato sulle fonti memorizzate", timeout=10000
            )
            assert not errors
            browser.close()
    finally:
        conn.close()
