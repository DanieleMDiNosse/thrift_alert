"""Opt-in real Firefox GUI journeys, isolated DB; never contacts marketplaces."""
import os
import pytest
from tests.test_gui import gui, request

pytestmark = pytest.mark.skipif(os.environ.get('MARKETWATCH_BROWSER') != '1',
                                reason='explicit browser validation only')


def test_browser_create_edit_toggle_remove_and_mobile(gui, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        page = browser.new_page(viewport={'width':1280, 'height':900})
        page.set_default_timeout(5000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        page.get_by_role('button', name='Nuova ricerca', exact=True).first.click()
        page.locator('#watch-name').fill('Fotocamera test GUI')
        page.locator('#source-subito').check()
        page.locator('#url-subito').fill('https://www.subito.it/annunci-italia/vendita/usato/?q=sony')
        page.locator('#max-price').fill('550')
        page.locator('#include-words').fill('sony\na6400')
        page.locator('#watch-enabled').uncheck()
        page.locator('#save-watch').click()
        page.get_by_text('Fotocamera test GUI', exact=True).wait_for()
        page.get_by_role('button', name='Modifica', exact=True).first.click()
        assert page.locator('#max-price').input_value() == '550'
        page.locator('#watch-name').fill('Fotocamera modificata')
        page.locator('#save-watch').click()
        page.get_by_text('Fotocamera modificata', exact=True).wait_for()
        page.get_by_role('button', name='Attiva', exact=True).first.click()
        page.get_by_role('button', name='Metti in pausa', exact=True).first.wait_for()
        expect(page.get_by_role('button', name='Metti in pausa', exact=True).first).to_be_enabled(timeout=2000)
        page.get_by_role('button', name='Metti in pausa', exact=True).first.click()
        page.get_by_role('button', name='Attiva', exact=True).first.wait_for()
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(tmp_path/'gui-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Elimina', exact=True).first.click()
        page.get_by_text('Fotocamera modificata', exact=True).wait_for(state='detached')
        assert not errors, errors
        browser.close()


def test_browser_keeps_long_lists_compact_and_explains_missing_urls(gui):
    from playwright.sync_api import sync_playwright, expect
    from subitoo.core import db
    from subitoo.core.models import Filters, Listing
    conn = db.init_db()
    qid = db.create_query(conn, name="Esempio", site="fake", search={}, filters=Filters(), cron="*/30 * * * *")
    for i in range(12):
        db.insert_listing(conn, qid, Listing(site_listing_id=str(i), title=f"Oggetto {i}", price=10))
    conn.close()
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{gui.server_port}")
        expect(page.locator("#listings .listing")).to_have_count(8)
        page.get_by_role("button", name="Mostra tutti (12)", exact=True).click()
        expect(page.locator("#listings .listing")).to_have_count(12)
        page.get_by_role("button", name="Mostra meno", exact=True).click()
        expect(page.locator("#listings .listing")).to_have_count(8)
        page.get_by_role("button", name="Nuova ricerca", exact=True).first.click()
        assert page.locator("#watch-name").get_attribute("maxlength") == "120"
        page.locator("#watch-name").fill("Senza URL")
        page.locator("#save-watch").click()
        expect(page.locator("#form-error")).to_contain_text("Incolla almeno un URL")
        browser.close()


def test_browser_preserves_custom_source_settings_and_modal_input(gui):
    import json
    from playwright.sync_api import sync_playwright
    from tests.test_gui import spec
    original = spec()
    original["sources"]["subito"].update(cron="15,45 * * * *", max_pages=3, enabled=False)
    original["sources"]["facebook"] = {"enabled":False, "search_url":"<unsupported>"}
    assert request(gui, "/api/watches/save", original)[0] == 200
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{gui.server_port}")
        page.get_by_role("button", name="Modifica", exact=True).click()
        assert page.locator("#cron-subito").input_value() == "15,45 * * * *"
        assert not page.locator("#source-subito").is_checked()
        page.locator("#watch-name").fill("Modifica senza perdita")
        # Advance the browser timer to exercise polling while the modal is open.
        page.clock.install()
        page.clock.fast_forward(31000)
        assert page.locator("#watch-name").input_value() == "Modifica senza perdita"
        page.locator("#save-watch").click()
        page.get_by_text("Modifica senza perdita", exact=True).wait_for()
        saved = json.loads(request(gui, "/api/state")[1])["watches"][0]
        assert saved["sources"]["subito"]["max_pages"] == 3
        assert saved["sources"]["subito"]["cron"] == "15,45 * * * *"
        assert not saved["sources"]["subito"]["enabled"]
        assert saved["sources"]["facebook"]["search_url"] == "<unsupported>"
        browser.close()


def test_browser_treats_saved_text_as_text_not_html(gui):
    from playwright.sync_api import sync_playwright
    from tests.test_gui import spec
    malicious = {**spec(), 'name':'<img src=x onerror=alert(1)>'}
    assert request(gui, '/api/watches/save', malicious)[0] == 200
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        page = browser.new_page()
        dialogs = []
        page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        page.get_by_text(malicious['name'], exact=True).wait_for()
        assert page.locator('img').count() == 0
        assert dialogs == []
        browser.close()
