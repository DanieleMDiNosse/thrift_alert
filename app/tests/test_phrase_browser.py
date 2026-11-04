"""Browser multiple phrases and per-source observed scan coverage."""
import json
import os
import pytest
from tests.test_gui import gui,request
from subitoo.core import db

pytestmark=pytest.mark.skipif(os.environ.get('MARKETWATCH_BROWSER')!='1',reason='explicit browser validation')


def test_multiple_phrase_editor_roundtrip_and_partial_coverage(gui):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.firefox.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':844})
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        page.get_by_role('button',name='Nuova ricerca',exact=True).first.click()
        phrases='casse attive\ncassa attiva\ndiffusori attivi'
        assert page.locator('#search-text').evaluate('(e)=>e.tagName')=='TEXTAREA'
        page.locator('#search-text').fill(phrases)
        page.locator('#min-price').fill('40'); page.locator('#max-price').fill('100')
        page.locator('#watch-enabled').uncheck()
        page.locator('#save-watch').click()
        page.get_by_role('button',name='Modifica',exact=True).wait_for()
        state=json.loads(request(gui,'/api/state')[1])
        assert state['watches'][0]['search_phrases']==phrases.splitlines()
        qid=state['watches'][0]['queries'][0]['id']
        conn=db.init_db()
        conn.execute('INSERT OR REPLACE INTO scan_coverage (query_id,coverage_json,scanned_at) VALUES (?,?,?)',
                     (qid,json.dumps({'complete':False,'pages':10,'phrases':3,'reason':'page_limit'}),1)); conn.close()
        page.reload()
        expect(page.get_by_text('Limite pagine raggiunto',exact=False)).to_be_visible()
        page.get_by_role('button',name='Modifica',exact=True).click()
        assert page.locator('#search-text').input_value()==phrases
        page.locator('#save-watch').click()
        page.locator('#watch-dialog').wait_for(state='hidden')
        assert json.loads(request(gui,'/api/state')[1])['watches'][0]['search_phrases']==phrases.splitlines()
        browser.close()
