"""Queue and scanner UX contracts: intercepted HTTP only, no app data writes."""
import json
import os

import pytest
from tests.test_ai_browser import ai_page, WEB

pytestmark = pytest.mark.skipif(os.environ.get("MARKETWATCH_BROWSER") != "1", reason="explicit browser validation")


def test_queue_summary_is_global_and_details_are_bounded(ai_page):
    from playwright.sync_api import expect
    page, state, _, errors = ai_page
    state["jobs"] = [{"id": i, "kind": "identify", "status": "queued", "title": f"Annuncio {i}", "updated_at": i} for i in range(250)]
    state["job_summary"] = {"queued": 500, "running": 1, "done": 30, "failed": 2, "cancelled": 4}
    page.get_by_role("button", name="Ricalcola classifiche").click()
    expect(page.get_by_role("status")).to_contain_text("In coda: 500")
    expect(page.get_by_role("status")).to_contain_text("In corso: 1")
    expect(page.get_by_role("status")).to_contain_text("Completati: 30")
    expect(page.get_by_role("status")).to_contain_text("Falliti: 2")
    expect(page.get_by_role("status")).to_contain_text("Annullati: 4")
    assert len(page.get_by_role("status").inner_text()) < 220
    expect(page.locator(".ai-job")).to_have_count(10)
    expect(page.locator(".ai-job").first).not_to_be_visible()
    page.get_by_text("Ultime attività AI", exact=True).click()
    expect(page.locator(".ai-job").first).to_contain_text("Annuncio 249")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors


def test_queue_fallback_pause_resume_and_specific_retry(ai_page):
    from playwright.sync_api import expect
    page, state, calls, errors = ai_page
    state["jobs"] = [{"id": 42, "kind": "identify", "status": "failed", "title": "<img src=x>", "error": "Modello non disponibile", "updated_at": 1}, {"id": 43, "kind": "identify", "status": "queued"}]
    page.get_by_role("button", name="Ricalcola classifiche").click()
    expect(page.get_by_role("status")).to_contain_text("In coda: 1")
    with page.expect_response("**/api/ai/settings"):
        page.get_by_role("button", name="Metti in pausa AI", exact=True).click()
    expect(page.get_by_role("button", name="Riprendi AI", exact=True)).to_be_enabled()
    expect(page.get_by_role("status")).to_contain_text("AI in pausa")
    page.get_by_text("Ultime attività AI", exact=True).click()
    expect(page.get_by_text("Modello non disponibile", exact=True)).to_be_visible()
    with page.expect_response("**/api/ai/jobs/42/retry"):
        page.get_by_role("button", name="Riprova attività 42", exact=True).click()
    assert next(c[2] for c in calls if c[1] == "/api/ai/jobs/42/retry") == {}
    assert not state["settings"]["enabled"]
    assert page.locator("#ai-assistant img").count() == 0
    with page.expect_response("**/api/ai/settings"):
        page.get_by_role("button", name="Riprendi AI", exact=True).click()
    expect(page.get_by_role("button", name="Metti in pausa AI", exact=True)).to_be_enabled()
    assert [c[2] for c in calls if c[1] == "/api/ai/settings"] == [{"enabled": False}, {"enabled": True}]
    assert not errors


def test_analysis_is_bounded_to_selected_watch(ai_page):
    page, _, calls, _ = ai_page
    with page.expect_response("**/api/ai/sync"):
        page.get_by_role("button", name="Analizza annunci", exact=True).click()
    page.get_by_label("Ricerca monitorata").select_option("3")
    with page.expect_response("**/api/ai/sync"):
        page.get_by_role("button", name="Analizza annunci", exact=True).click()
    assert [c[2] for c in calls if c[1] == "/api/ai/sync"] == [{"watch_id": None, "limit": 5}, {"watch_id": 3, "limit": 5}]


@pytest.mark.parametrize("error_code, message", [("timeout", "Tempo limite"), ("access_denied", "Accesso negato"), ("fetch_error", "Errore imprevisto")])
def test_failed_scan_labels_previous_coverage_without_retry(error_code, message):
    from playwright.sync_api import sync_playwright, expect
    query = {"id": 7, "site": "subito", "status": "error", "seeded": True, "last_run_at": 1700000000, "error": None,
             "coverage": {"complete": False, "reason": "page_limit", "pages": 5, "phrases": 2}, "coverage_observed_at": 1700000000,
             "last_attempt": {"status": "error", "started_at": 1700000100, "finished_at": 1700000200, "error_code": error_code}}
    state = {"watches": [{"id": 3, "name": "Fotocamere", "enabled": True, "sources": {"subito": {"enabled": True}}, "queries": [query]}], "listings": []}
    calls, errors = [], []
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        def route(r):
            path = r.request.url.split("http://queue.test", 1)[-1]
            calls.append((r.request.method, path))
            if path == "/":
                r.fulfill(content_type="text/html", body=(WEB / "index.html").read_text())
            elif path.startswith("/static/"):
                file = WEB / path.rsplit("/", 1)[-1]
                r.fulfill(content_type="text/javascript" if file.suffix == ".js" else "text/css", body=file.read_text() if file.exists() else "")
            elif path == "/api/state":
                r.fulfill(json=state)
            elif path.startswith("/api/ai/state"):
                r.fulfill(json={})
            else:
                r.abort()
        page.route("**/*", route)
        page.clock.install()
        page.goto("http://queue.test/")
        expect(page.locator("#watches")).to_contain_text("Copertura precedente")
        expect(page.locator("#watches")).to_contain_text("Limite pagine raggiunto")
        expect(page.locator("#watches")).to_contain_text(message)
        expect(page.get_by_role("button", name="Riprova scansione", exact=True)).to_be_visible()
        page.clock.fast_forward(31000)
        assert not any(method == "POST" for method, _ in calls)
        query["last_attempt"] = {"status": "ok", "error_code": None}
        page.clock.fast_forward(31000)
        expect(page.locator("#watches")).not_to_contain_text("Copertura precedente")
        expect(page.locator("#watches")).to_contain_text("Limite pagine raggiunto")
        assert not errors
        browser.close()
