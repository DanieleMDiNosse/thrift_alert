"""Browser round-trip for the optional description criteria, on an isolated database."""
import json
import os

import pytest
from tests.test_gui import gui, request  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get('MARKETWATCH_BROWSER') != '1', reason='explicit browser validation')


@pytest.mark.parametrize('manual', [False, True])
def test_description_filter_create_edit_clear(gui, manual):  # noqa: F811 - imported pytest fixture
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        page.get_by_role('button', name='Nuova ricerca', exact=True).first.click()
        page.locator('#watch-enabled').uncheck()
        page.get_by_text('Opzioni avanzate', exact=True).click()
        field = page.get_by_label('Parole obbligatorie nella descrizione', exact=True)
        expect(field).to_be_visible(timeout=3000)
        field.fill('garanzia\ncome nuovo')
        if manual:
            page.locator('#custom-urls').check()
            page.locator('#watch-name').fill('camera manuale')
            page.locator('#url-subito').fill('https://www.subito.it/annunci-italia/vendita/usato/?q=sony')
        else:
            page.locator('#search-text').fill('sony')
        endpoint = '**/api/watches/save' if manual else '**/api/watches/quick-save'
        with page.expect_response(endpoint) as response:
            page.locator('#save-watch').click()
        assert response.value.status == 200
        expect(page.locator('#watch-dialog')).not_to_be_visible()
        saved = json.loads(request(gui, '/api/state')[1])['watches'][0]
        assert saved['description_include'] == ['garanzia', 'come nuovo']
        page.get_by_role('button', name='Modifica', exact=True).click()
        if not manual:
            page.get_by_text('Opzioni avanzate', exact=True).click()
        expect(field).to_have_value('garanzia\ncome nuovo')
        field.fill('')
        with page.expect_response(endpoint):
            page.locator('#save-watch').click()
        expect(page.locator('#watch-dialog')).not_to_be_visible()
        assert json.loads(request(gui, '/api/state')[1])['watches'][0]['description_include'] == []
        browser.close()
