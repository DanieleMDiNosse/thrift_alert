"""Opt-in, fully routed browser contract for the real inline worth module."""

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation"
)
WEB = Path(__file__).resolve().parents[1] / "subitoo" / "web"
LISTING = {
    "site": "subito",
    "site_listing_id": "native/42",
    "title": "Camera",
    "price": 80,
    "currency": "EUR",
}


def response(status="completed", **changes):
    data = {
        "status": status,
        "stale": False,
        "result": {
            "message": "Conviene solo se funziona.\n\nControlla la batteria.",
            "source_ids": ["shop"],
            "new_price": None,
        },
        "sources": [
            {
                "id": "shop",
                "title": "Negozio",
                "url": "https://shop.example/item",
                "retrieved_at": "2026-10-20",
            }
        ],
        "listing": LISTING,
        "analyzed_at": 100,
        "error": None,
        "job_id": None,
    }
    data.update(changes)
    return data


@pytest.fixture
def ui():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        state = {
            "get": response("not_analyzed", result=None),
            "post": response(),
            "requests": [],
            "abort": False,
        }
        html = (
            '<meta charset="utf-8"><meta name="csrf-token" content="fixture-csrf"><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/worth.css"><div id="rows"></div><script src="/worth.js"></script><script>window.draw=()=>{document.querySelector("#rows").replaceChildren();const row=document.createElement("article");row.className="listing";document.querySelector("#rows").append(row);if(window.WorthAssistant)WorthAssistant.decorate(row,'
            + json.dumps(LISTING)
            + ")};draw();</script>"
        )

        def route(req):
            url = req.request.url
            if "/api/ai/review" in url:
                state["requests"].append(
                    {
                        "method": req.request.method,
                        "url": url,
                        "headers": req.request.headers,
                        "body": req.request.post_data_json
                        if req.request.method == "POST"
                        else None,
                    }
                )
                if state["abort"]:
                    req.abort()
                else:
                    data = (
                        state["post"] if req.request.method == "POST" else state["get"]
                    )
                    if req.request.method == "POST" and "get_after_post" in state:
                        state["get"] = state["get_after_post"]
                    req.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(data),
                    )
            elif url.endswith(("/worth.js", "/worth.css", "/style.css")):
                path = WEB / url.rsplit("/", 1)[1]
                req.fulfill(
                    status=200,
                    content_type="text/javascript"
                    if path.suffix == ".js"
                    else "text/css",
                    body=path.read_text() if path.exists() else "",
                )
            elif url == "http://worth.test/":
                req.fulfill(status=200, content_type="text/html", body=html)
            else:
                req.abort()  # No real network, including citation targets.

        page.route("**/*", route)
        page.goto("http://worth.test/")
        yield page, state
        browser.close()


def open_panel(page):
    page.get_by_role("button", name="Recensisci", exact=True).click()


def test_explicit_click_posts_once_with_csrf_and_shows_snapshot(ui):
    from playwright.sync_api import expect

    page, state = ui
    assert state["requests"] == []
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo")
    posts = [r for r in state["requests"] if r["method"] == "POST"]
    assert len(posts) == 1
    assert posts[0]["body"] == {"site": "subito", "id": "native/42", "refresh": False}
    assert posts[0]["headers"]["x-csrf-token"] == "fixture-csrf"
    expect(page.locator(".worth-panel")).to_contain_text("Recensione del prodotto")
    expect(page.locator(".worth-panel")).to_contain_text("GPT-6.1-sol · low")
    expect(page.locator(".worth-panel")).not_to_contain_text("Prezzo del nuovo")
    expect(page.locator(".worth-panel")).not_to_contain_text("Spese")
    expect(
        page.get_by_role("button", name="Recensisci", exact=True)
    ).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_pending_polling_and_rerender_do_not_duplicate_post(ui):
    from playwright.sync_api import expect

    page, state = ui
    state["post"] = response("queued", result=None, job_id=7)
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("In coda")
    expect(
        page.get_by_role("button", name="Recensisci", exact=True)
    ).to_be_disabled()
    state["get"] = state["post"]
    with page.expect_response("**/api/ai/review?*"):
        page.evaluate("draw()")
    expect(page.locator(".worth-panel")).to_contain_text("In coda")
    state["get"] = response()
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo", timeout=7000)
    expect(
        page.get_by_role("button", name="Recensisci", exact=True)
    ).to_be_enabled()
    assert sum(r["method"] == "POST" for r in state["requests"]) == 1
    count = len(state["requests"])
    page.wait_for_timeout(2300)
    assert len(state["requests"]) == count


def test_reload_and_reopen_read_cache_without_automatic_post(ui):
    from playwright.sync_api import expect

    page, state = ui
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo")
    state["get"] = response()
    page.reload()
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo")
    page.get_by_role("button", name="Chiudi recensione").click()
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo")
    assert sum(r["method"] == "POST" for r in state["requests"]) == 1


def test_rapid_clicks_do_not_enqueue_duplicates(ui):
    from playwright.sync_api import expect

    page, state = ui
    page.evaluate("""() => {
        const trigger = document.querySelector('.worth-assistant > button');
        for (let i = 0; i < 8; i++) trigger.click();
    }""")
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo")
    assert sum(r["method"] == "POST" for r in state["requests"]) == 1


def test_remembered_selection_never_enqueues_missing_cache_on_reload(ui):
    from playwright.sync_api import expect

    page, state = ui
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo")
    # A removed/expired report is still only read automatically.
    state["get"] = response("not_analyzed", result=None)
    page.reload()
    expect(page.get_by_role("button", name="Riprova recensione")).to_be_visible()
    assert sum(r["method"] == "POST" for r in state["requests"]) == 1


def test_failed_refresh_retains_previous_result_and_retries(ui):
    from playwright.sync_api import expect

    page, state = ui
    state["get"] = response(stale=True)
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("non aggiornata")
    state["post"] = response("failed", result=None, error="Modello offline")
    page.get_by_role("button", name="Aggiorna recensione").click()
    expect(page.locator(".worth-panel")).to_contain_text("Modello offline")
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo")
    state["post"] = response()
    page.get_by_role("button", name="Aggiorna recensione").click()
    expect(page.locator(".worth-error")).to_have_count(0)
    assert all(r["body"]["refresh"] for r in state["requests"] if r["method"] == "POST")


@pytest.mark.parametrize(
    "kind,label,availability,stock",
    [
        ("current_new", "Nuovo attuale", "in_stock", "Disponibile"),
        (
            "equivalent_new",
            "Nuovo equivalente",
            "unknown",
            "Disponibilità non verificata",
        ),
        ("historical_list", "Listino storico", "discontinued", "Fuori produzione"),
    ],
)
def test_new_price_labels_and_retrieved_citations_only(
    ui, kind, label, availability, stock
):
    from playwright.sync_api import expect

    page, state = ui
    state["get"] = response(
        result={
            "message": "<script>window.pwned=1</script>\n\n[evil](https://evil.example) <img src=x onerror=alert(1)>",
            "source_ids": ["shop", "bad", "missing"],
            "new_price": {
                "amount": 150,
                "currency": "EUR",
                "source_id": "shop",
                "quote": "Prezzo del negozio",
                "kind": kind,
                "availability": availability,
            },
        },
        sources=[
            {
                "id": "shop",
                "title": "Negozio",
                "url": "https://shop.example/item",
                "retrieved_at": "2026-10-20",
            },
            {
                "id": "bad",
                "title": "Non sicuro",
                "url": "https://user:password@evil.example/",
                "retrieved_at": None,
            },
            {
                "id": "unused",
                "title": "Non citato",
                "url": "https://unused.example/",
                "retrieved_at": None,
            },
        ],
    )
    open_panel(page)
    panel = page.locator(".worth-panel")
    expect(panel).not_to_contain_text(label)
    expect(panel).not_to_contain_text(stock)
    expect(panel).to_contain_text("<script>")
    assert panel.locator("script,img").count() == 0
    assert page.evaluate("window.pwned") is None
    assert panel.locator("a").evaluate_all("nodes => nodes.map(n=>n.href)") == [
        "https://shop.example/item"
    ]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_offline_get_can_retry_without_post(ui):
    from playwright.sync_api import expect

    page, state = ui
    state["abort"] = True
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("Connessione")
    state["abort"] = False
    state["get"] = response()
    page.get_by_role("button", name="Riprova caricamento").click()
    expect(page.locator(".worth-panel")).to_contain_text("Conviene solo")
    assert not any(r["method"] == "POST" for r in state["requests"])


def test_polling_is_bounded_even_across_row_replacement(ui):
    from playwright.sync_api import expect

    page, state = ui
    page.evaluate("""() => {
        const original = window.setTimeout;
        window.setTimeout = (fn, delay, ...args) => original(fn, delay === 2000 ? 10 : delay, ...args);
        const fetchOriginal = window.fetch;
        window.fixtureGets = 0;
        window.fetch = (...args) => {
            if (args[1].method === 'GET') window.fixtureGets++;
            return fetchOriginal(...args);
        };
    }""")
    state["post"] = response("queued", result=None)
    state["get_after_post"] = state["post"]
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("In coda")
    state["get"] = state["post"]
    page.wait_for_function("window.fixtureGets >= 15")
    page.evaluate("draw()")
    expect(page.locator(".worth-panel")).to_contain_text(
        "Controllo automatico sospeso", timeout=10000
    )
    # Initial read, 60 automatic polls, at most one re-render read. Re-render must not reset the budget.
    assert sum(r["method"] == "GET" for r in state["requests"]) <= 62
    count = len(state["requests"])
    page.wait_for_timeout(100)
    assert len(state["requests"]) == count


def test_closed_or_removed_panel_stops_pending_polls(ui):
    from playwright.sync_api import expect

    page, state = ui
    state["post"] = response("running", result=None)
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("Analisi in corso")
    page.get_by_role("button", name="Chiudi recensione").click()
    count = len(state["requests"])
    page.wait_for_timeout(2300)
    assert len(state["requests"]) == count
    state["get"] = response("running", result=None)
    open_panel(page)
    expect(page.locator(".worth-panel")).to_contain_text("Analisi in corso")
    page.evaluate("document.querySelector('#rows').replaceChildren()")
    count = len(state["requests"])
    page.wait_for_timeout(2300)
    assert len(state["requests"]) == count


def test_review_panel_shows_model_release_year_reference_price_and_comparison_links(ui):
    from playwright.sync_api import expect

    page, state = ui
    state["get"] = response(
        result={
            "message": "Telaio affidabile per lunghi giri su ghiaia.",
            "source_ids": [],
            "new_price": None,
            "product_name": "Trek Checkpoint ALR 5",
            "release_year": 2021,
            "reference_price_eur": 200,
            "reference_price_kind": "launch_msrp",
        },
        sources=[],
    )
    open_panel(page)
    panel = page.locator(".worth-panel")
    expect(panel.locator(".worth-specs")).to_contain_text("Trek Checkpoint ALR 5")
    expect(panel.locator(".worth-specs")).to_contain_text("2021")
    expect(panel.locator(".worth-specs")).to_contain_text("-60% l’annuncio")
    links = panel.locator(".worth-compare-link").evaluate_all("nodes => nodes.map(n => n.href)")
    assert links == [
        "https://www.trovaprezzi.it/prezzo_Trek%20Checkpoint%20ALR%205.aspx",
        "https://www.idealo.it/risultati.html?q=Trek%20Checkpoint%20ALR%205",
    ]
