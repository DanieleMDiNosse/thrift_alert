"""Deterministic Firefox contract tests; no model/network requests."""

import copy
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation"
)
WEB = Path(__file__).resolve().parents[1] / "subitoo" / "web"


@pytest.fixture
def ai_page():
    from playwright.sync_api import sync_playwright

    state = {
        "products": [
            {
                "id": "sony",
                "name": "Sony A6400",
                "category": "camera",
                "confidence": 0.8,
                "research_status": "complete",
                "research": {
                    "summary": "**Ottima** fotocamera <img src=x onerror=alert(1)>",
                    "criteria": {"autofocus": {"score": 8, "source_ids": ["s1"]}},
                    "strengths": ["AF rapido"],
                    "weaknesses": ["No IBIS"],
                    "known_issues": ["Batteria piccola"],
                    "specifications": {"sensor": "APS-C"},
                    "confidence": 0.8,
                },
                "sources": [
                    {
                        "id": "s1",
                        "title": "Review verificata",
                        "url": "https://example.com/review",
                        "source_type": "review",
                        "retrieved_at": "2026-10-20",
                    },
                    {"id": "bad", "title": "Unsafe", "url": "javascript:alert(1)"},
                ],
            },
            {
                "id": "canon",
                "name": "Canon R50",
                "category": "camera",
                "research": {},
                "sources": [],
            },
        ],
        "listings": [
            {
                "id": 12,
                "product_id": "sony",
                "title": "Sony usata",
                "price": 400,
                "currency": "EUR",
                "url": "https://example.com/listing",
                "condition": "used",
            },
            {
                "id": 13,
                "product_id": "canon",
                "title": "Canon usata",
                "price": 450,
                "currency": "EUR",
            },
        ],
        "rankings": {
            "quality": [
                {"product_id": "sony", "quality_score": 8},
                {"product_id": "canon", "quality_score": None},
            ],
            "value": [{"listing_id": 12, "product_id": "sony", "value_score": 7}],
            "deal": [
                {
                    "listing_id": 12,
                    "product_id": "sony",
                    "deal_score": 9,
                    "market": {"median": 500, "n": 3},
                    "warnings": ["Campione ridotto"],
                }
            ],
        },
        "conversations": [
            {
                "id": 7,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Cronologia salvata **verificata** [falso](javascript:alert(1))",
                    }
                ],
            }
        ],
        "jobs": [],
        "settings": {
            "enabled": True,
            "endpoint": "http://localhost:11434/v1",
            "model": "local-model",
        },
    }
    calls, errors = [], []
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(request_route):
            request = request_route.request
            path = request.url.split("http://ai.test", 1)[-1]
            if path == "/":
                request_route.fulfill(
                    headers={
                        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'"
                    },
                    content_type="text/html",
                    body='<meta charset="utf-8"><meta name="csrf-token" content="test-token"><link rel="stylesheet" href="/static/ai.css"><section id="ai-assistant"></section><script src="/static/ai.js" defer></script>',
                )
            elif path.startswith("/static/"):
                file = WEB / path.rsplit("/", 1)[-1]
                request_route.fulfill(
                    content_type="text/javascript"
                    if file.suffix == ".js"
                    else "text/css",
                    body=file.read_text() if file.exists() else "",
                )
            elif path.startswith("/api/"):
                payload = request.post_data_json if request.method == "POST" else None
                calls.append((request.method, path, payload, request.headers))
                if (
                    request.method == "GET"
                    and path.startswith("/api/ai/state")
                    and state.get("_reply_after")
                ):
                    state["_reply_after"] -= 1
                    if not state["_reply_after"]:
                        state["conversations"][-1]["messages"].append(
                            {"role": "assistant", "content": "Risposta dal database"}
                        )
                response = copy.deepcopy(state)
                status = 200
                if path == "/api/state":
                    response = {"watches": [{"id": 3, "name": "Fotocamere"}]}
                elif request.method == "POST":
                    response = {"status": "queued"}
                    if path == "/api/ai/compare":
                        response = {
                            "products": copy.deepcopy(state["products"]),
                            "listings": copy.deepcopy(state["listings"]),
                            "rankings": copy.deepcopy(state["rankings"]),
                            "summary": "Confronto supportato",
                            "warnings": ["Dati incompleti"],
                        }
                    elif path == "/api/ai/conversations/new":
                        state["conversations"].append({"id": 8, "messages": []})
                        response = {"id": 8}
                    elif path.endswith("/messages"):
                        state["_reply_after"] = 3
                    elif path.endswith("/clear"):
                        next(
                            c
                            for c in state["conversations"]
                            if str(c["id"]) == path.split("/")[-2]
                        )["messages"] = []
                    elif path == "/api/ai/settings":
                        state["settings"]["enabled"] = payload["enabled"]
                    elif path == "/api/ai/sync" and state.get("fail_sync"):
                        status, response = 503, {"error": "Modello non disponibile"}
                request_route.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(response),
                )
            else:
                request_route.abort()

        page.route("**/*", route)
        page.goto("http://ai.test/")
        yield page, state, calls, errors
        browser.close()


def test_mixed_category_catalog_retains_scores_listings_and_platforms(ai_page):
    from playwright.sync_api import expect

    page, state, _, errors = ai_page
    state["products"].append(
        {
            "id": "amp",
            "name": "Amplificatore",
            "category": "audio",
            "confidence": 0.8,
            "research": {},
            "sources": [],
        }
    )
    state["listings"][0]["site"] = "subito"
    state["listings"][1]["site"] = "vinted"
    state["listings"].append(
        {
            "id": 14,
            "product_id": "amp",
            "title": "Amplificatore usato",
            "price": 80,
            "currency": "EUR",
            "site": "wallapop",
            "url": "https://example.com/amp",
        }
    )
    evals = [
        dict(
            listing,
            listing_id=listing["id"],
            quality_score=82 if listing["id"] == 12 else None,
            value_score=None,
            deal_score=None,
        )
        for listing in state["listings"]
    ]
    state["rankings"] = {
        "quality": [],
        "value": [],
        "deal": [],
        "listings": evals,
        "by_category": {
            "camera": {
                "quality": [{"product_id": "sony", "quality_score": 82}],
                "listings": evals[:2],
            },
            "audio": {"quality": [], "listings": evals[2:]},
        },
    }
    page.reload()
    ranks = page.locator(".ai-ranks")
    expect(ranks).to_contain_text("Qualità: 82")
    expect(ranks.get_by_role("link", name="Sony usata")).to_be_visible()
    expect(ranks.get_by_role("heading", name="Canon R50")).to_be_visible()
    expect(
        ranks.get_by_role("heading", name="Amplificatore", exact=True)
    ).to_be_visible()
    expect(page.locator(".ai-platform-summary")).to_contain_text("Vinted: 1")
    page.get_by_label("Marketplace AI").select_option("vinted")
    expect(ranks.get_by_role("heading", name="Canon R50")).to_be_visible()
    expect(ranks.get_by_role("heading", name="Sony A6400")).to_have_count(0)
    assert not errors


def test_unknown_total_has_explicit_separate_asking_price_view(ai_page):
    from playwright.sync_api import expect

    page, state, _, _ = ai_page
    row = dict(
        state["listings"][0],
        listing_id=12,
        quality_score=80,
        value_score=None,
        asking_value_score=12,
        effective_price=None,
        value_basis="asking_price_only",
    )
    state["rankings"]["value"] = []
    state["rankings"]["asking_value"] = [row]
    state["rankings"]["listings"] = [row]
    page.reload()
    page.get_by_label("Classifica").select_option("value")
    expect(page.locator(".ai-ranks")).to_contain_text("Indicatore sul prezzo richiesto")
    expect(page.locator(".ai-ranks")).to_contain_text(
        "spedizione e commissioni escluse"
    )
    expect(page.locator(".ai-ranks")).to_contain_text("12")


def test_professional_and_user_research_are_both_visible(ai_page):
    from playwright.sync_api import expect

    page, state, _, _ = ai_page
    state["products"][0]["research"].update(
        professional_summary="Risultati della prova tecnica",
        user_summary="Esperienze di utilizzo prolungato",
    )
    page.reload()
    page.get_by_role("button", name="Dettagli Sony A6400").click()
    expect(page.get_by_role("heading", name="Recensioni professionali")).to_be_visible()
    expect(
        page.get_by_text("Risultati della prova tecnica", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_text("Esperienze di utilizzo prolungato", exact=True)
    ).to_be_visible()


def test_rank_filters_detail_sources_and_mobile(ai_page):
    from playwright.sync_api import expect

    page, state, calls, errors = ai_page
    expect(page.get_by_role("heading", name="Assistente acquisti AI")).to_be_visible()
    page.get_by_label("Categoria").select_option("camera")
    page.get_by_label("Ricerca monitorata").select_option("3")
    page.get_by_label("Classifica").select_option("deal")
    expect(page.get_by_text("Campione ridotto", exact=True)).to_be_visible()
    page.get_by_role("button", name="Dettagli Sony A6400").click()
    expect(page.get_by_text("AF rapido", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Review verificata")).to_have_attribute(
        "href", "https://example.com/review"
    )
    assert page.locator('a[href^="javascript:"]').count() == 0
    assert page.locator("#ai-assistant img").count() == 0
    assert any(
        "category=camera" in path and "watch_id=3" in path for _, path, _, _ in calls
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors


def test_comparison_requires_two_products_and_sends_context(ai_page):
    from playwright.sync_api import expect

    page, _, calls, _ = ai_page
    compare = page.get_by_role("button", name="Confronta selezionati")
    expect(compare).to_be_disabled()
    page.get_by_label("Seleziona Sony A6400").check()
    expect(compare).to_be_disabled()
    page.get_by_label("Seleziona Canon R50").check()
    compare.click()
    expect(page.get_by_text("Confronto supportato", exact=True)).to_be_visible()
    assert next(c[2] for c in calls if c[1] == "/api/ai/compare") == {
        "product_ids": ["sony", "canon"]
    }


def test_persistent_chat_queued_reply_and_clear(ai_page):
    from playwright.sync_api import expect

    page, _, calls, errors = ai_page
    page.get_by_label("Conversazione").select_option("7")
    expect(page.get_by_text("Cronologia salvata", exact=False)).to_be_visible()
    page.reload()
    expect(page.get_by_label("Conversazione")).to_have_value("7")
    page.get_by_label("Seleziona Sony A6400").check()
    page.get_by_role("button", name="Nuova conversazione").click()
    expect(page.get_by_label("Conversazione")).to_have_value("8")
    page.get_by_label("Messaggio").fill("E a un prezzo diverso?")
    page.get_by_label("Ricerca nuove fonti").check()
    page.get_by_text(
        "Prezzi ipotetici (non modificano gli annunci)", exact=True
    ).click()
    page.get_by_label("Prezzo ipotetico Sony usata").fill("350")
    page.get_by_role("button", name="Invia messaggio").click()
    expect(page.get_by_role("status")).to_contain_text("Messaggio accodato")
    page.get_by_label("Messaggio").fill("Bozza da conservare durante il polling")
    expect(page.get_by_text("Risposta dal database", exact=True)).to_be_visible(
        timeout=10000
    )
    expect(page.get_by_label("Messaggio")).to_have_value(
        "Bozza da conservare durante il polling"
    )
    expect(page.get_by_label("Prezzo ipotetico Sony usata")).to_have_value("350")
    payload = next(c[2] for c in calls if c[1].endswith("/messages"))
    assert payload == {
        "message": "E a un prezzo diverso?",
        "research": True,
        "hypothetical": {"12": 350},
    }
    assert next(c[2] for c in calls if c[1].endswith("/new"))["product_ids"] == ["sony"]
    page.get_by_role("button", name="Svuota conversazione").click()
    expect(page.get_by_text("Risposta dal database", exact=True)).not_to_be_visible()
    assert page.evaluate("Object.keys(localStorage)") == ["subitoo.ai.conversation"]
    assert all(
        c[3].get("x-csrf-token") == "test-token" for c in calls if c[0] == "POST"
    )
    assert not errors


def test_controls_settings_refresh_and_failure_recovery(ai_page):
    from playwright.sync_api import expect

    page, state, calls, errors = ai_page
    page.get_by_text("Impostazioni AI e stato", exact=True).click()
    expect(page.get_by_text("local-model", exact=False)).to_be_visible()
    page.get_by_label("AI abilitata").uncheck()
    expect(page.get_by_label("AI abilitata")).not_to_be_checked()
    page.get_by_role("button", name="Dettagli Sony A6400").click()
    page.get_by_role("button", name="Aggiorna ricerca").click()
    expect(page.get_by_role("button", name="Aggiorna ricerca")).to_be_enabled()
    assert any(c[1] == "/api/ai/products/sony/refresh" for c in calls)
    state["fail_sync"] = True
    page.get_by_role("button", name="Analizza annunci").click()
    expect(page.get_by_role("alert")).to_contain_text("Modello non disponibile")
    expect(page.get_by_role("button", name="Analizza annunci")).to_be_enabled()
    state["fail_sync"] = False
    page.get_by_role("button", name="Analizza annunci").click()
    expect(page.get_by_role("alert")).to_be_empty()
    assert not errors


def test_empty_null_state_and_unavailable_storage(ai_page):
    from playwright.sync_api import expect

    page, state, _, errors = ai_page
    state.update(
        products=None,
        listings=None,
        rankings=None,
        conversations=None,
        jobs=None,
        settings=None,
    )
    page.add_init_script(
        "Object.defineProperty(window, 'localStorage', {get() {throw new Error('denied')}})"
    )
    page.reload()
    expect(page.get_by_text("Nessun prodotto analizzato.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Confronta selezionati")).to_be_disabled()
    expect(page.get_by_role("button", name="Invia messaggio")).to_be_disabled()
    assert not errors


def test_comparison_renders_real_criteria_and_chat_citations(ai_page):
    from playwright.sync_api import expect

    page, state, calls, errors = ai_page
    state["conversations"][0]["messages"][0]["source_ids"] = ["s1"]
    page.get_by_role("button", name="Ricalcola classifiche").click()
    page.get_by_label("Seleziona Sony A6400").check()
    page.get_by_label("Seleziona Canon R50").check()
    page.get_by_role("button", name="Confronta selezionati").click()
    panel = page.get_by_role("region", name="Confronto prodotti")
    expect(panel.get_by_role("table")).to_be_visible()
    expect(panel.get_by_role("cell", name="autofocus", exact=True)).to_be_visible()
    page.get_by_label("Conversazione").select_option("7")
    expect(
        page.get_by_role("log").get_by_role("link", name="Review verificata")
    ).to_have_attribute("href", "https://example.com/review")
    assert not errors


def test_listing_facts_editor_uses_explicit_unknowns_and_csrf(ai_page):

    page, state, calls, errors = ai_page
    page.get_by_role("button", name="Dettagli Sony A6400").click()
    page.get_by_text("Dati verificati annuncio 12", exact=True).click()
    page.get_by_label("Condizione 12").select_option("good")
    page.get_by_label("Spedizione 12").fill("5")
    page.get_by_label("Commissioni 12").fill("0")
    with page.expect_response("**/api/ai/listings/12/facts"):
        page.get_by_role("button", name="Salva dati 12").click()
    payload = next(c[2] for c in calls if c[1] == "/api/ai/listings/12/facts")
    assert (
        payload["shipping_cost"] == 5
        and payload["fees"] == 0
        and payload["condition"] == "good"
    )
    assert not errors


def test_gpt_configuration_error_visible_without_exposing_key(ai_page):
    from playwright.sync_api import expect

    page, state, _, errors = ai_page
    state["settings"].update(
        provider="openai",
        model="gpt-6.1-sol",
        reasoning_effort="low",
        configuration_error="Configura AI_API_KEY sul backend per usare GPT.",
    )
    page.get_by_role("button", name="Ricalcola classifiche").click()
    expect(page.get_by_role("status")).to_contain_text("Configura AI_API_KEY")
    page.get_by_text("Impostazioni AI e stato", exact=True).click()
    expect(page.get_by_text("Provider: openai", exact=False)).to_contain_text(
        "Reasoning: low"
    )
    assert not errors
