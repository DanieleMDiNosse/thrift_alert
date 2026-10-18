"""Real browser: ordinary setup needs product text and price, never URLs."""
import json
import os
import pytest
from tests.test_gui import gui, request

pytestmark=pytest.mark.skipif(os.environ.get('MARKETWATCH_BROWSER')!='1', reason='explicit browser validation')


def test_edit_does_not_silently_enable_unselected_marketplaces(gui):
    from playwright.sync_api import sync_playwright
    assert request(gui,"/api/watches/quick-save", {"query":"bici", "enabled":False, "sources":{"subito":{"enabled":True}}})[0]==200
    with sync_playwright() as p:
        browser=p.firefox.launch(headless=True)
        page=browser.new_page()
        page.goto(f"http://127.0.0.1:{gui.server_port}")
        page.get_by_role("button",name="Modifica",exact=True).click()
        page.get_by_text("Opzioni avanzate",exact=True).click()
        assert not page.locator("#source-vinted").is_checked()
        assert not page.locator("#source-wallapop").is_checked()
        page.locator("#save-watch").click()
        page.locator("#watch-dialog").wait_for(state="hidden")
        state=json.loads(request(gui,"/api/state")[1])["watches"][0]
        assert len(state["queries"])==1
        browser.close()


def test_product_and_price_are_sufficient_in_browser(gui):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser=p.firefox.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':844})
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        page.get_by_role('button',name='Nuova ricerca',exact=True).first.click()
        page.locator('#search-text').fill('Sony a6400')
        page.locator('#min-price').fill('100')
        page.locator('#max-price').fill('550')
        page.locator('#watch-enabled').uncheck()
        assert not page.locator('#url-subito').is_visible()
        assert not page.locator('#watch-name').is_visible()
        assert 'URL' not in page.locator('#watch-dialog').inner_text()
        page.locator('#save-watch').click()
        page.get_by_text('Sony a6400',exact=True).wait_for()
        state=json.loads(request(gui,'/api/state')[1])['watches'][0]
        assert state['search_text']=='Sony a6400' and len(state['queries'])==3
        assert state['min_price_eur']==100 and state['max_price_eur']==550
        page.get_by_role('button',name='Modifica',exact=True).click()
        assert page.locator('#search-text').input_value()=='Sony a6400'
        assert not page.locator('#url-wallapop').is_visible()
        page.locator('#search-text').fill('Sony a6700')
        page.locator('#save-watch').click()
        page.get_by_text('Sony a6700',exact=True).wait_for()
        saved=json.loads(request(gui,'/api/state')[1])['watches'][0]
        assert saved['title_include']==['Sony','a6700']
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        browser.close()
