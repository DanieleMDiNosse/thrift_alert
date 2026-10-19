"""Opt-in browser description preview/expand and on-demand cache journeys."""
import os
import pytest
from tests.test_gui import gui
from tests.test_descriptions import listing
from subitoo.core import db

pytestmark=pytest.mark.skipif(os.environ.get('MARKETWATCH_BROWSER')!='1',reason='explicit browser validation')


def test_native_description_preview_expands_and_collapses(gui):
    from playwright.sync_api import sync_playwright,expect
    text='Prima riga della descrizione\n'+'Dettaglio completo della fotocamera.\n'*12+'Ultima riga'
    conn=db.init_db(); listing(conn,site='subito',raw={'body':text}); conn.close()
    with sync_playwright() as p:
        browser=p.firefox.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':844})
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        description=page.locator('.listing-description')
        expect(description).to_have_text(text)
        assert description.evaluate('(e)=>e.scrollHeight>e.clientHeight')
        page.get_by_role('button',name='Mostra tutto',exact=True).click()
        assert description.evaluate('(e)=>e.scrollHeight<=e.clientHeight+1')
        page.get_by_role('button',name='Mostra meno',exact=True).click()
        assert description.evaluate('(e)=>e.scrollHeight>e.clientHeight')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        browser.close()


def test_vinted_loads_only_on_click_and_cached_after_reload(gui,monkeypatch):
    from playwright.sync_api import sync_playwright,expect
    from subitoo.core import descriptions
    calls=[]
    def fetch(*args):
        calls.append(args); return 'Descrizione caricata dal dettaglio\n<img src=x onerror=alert(1)>\nTesto completo'
    monkeypatch.setattr(descriptions,'fetch_vinted_description',fetch)
    conn=db.init_db(); listing(conn); conn.close()
    with sync_playwright() as p:
        browser=p.firefox.launch(headless=True)
        page=browser.new_page()
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        load=page.get_by_role('button',name='Carica descrizione',exact=True)
        load.wait_for()
        assert calls==[]
        load.click()
        expect(page.locator('.listing-description')).to_contain_text('Descrizione caricata')
        assert len(calls)==1 and page.locator('img').count()==0
        page.reload()
        expect(page.locator('.listing-description')).to_contain_text('Descrizione caricata')
        assert len(calls)==1
        browser.close()
