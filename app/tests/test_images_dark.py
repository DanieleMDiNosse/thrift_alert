"""Photos use known HTTPS image CDNs, no server-side image proxy."""
import json
import os
import pytest
from tests.test_gui import gui, request
from subitoo.core import db
from subitoo.core.models import Filters, Listing


def populate(image_url):
    conn=db.init_db()
    qid=db.create_query(conn,name='Foto',site='vinted',search={},filters=Filters(),cron='*/30 * * * *')
    db.insert_listing(conn,qid,Listing(site_listing_id='photo',title='Fotocamera',price=150,
        image_url=image_url,url='https://www.vinted.it/items/123-fotocamera'))
    conn.close()


@pytest.mark.parametrize('url', ['https://images1.vinted.net/a.jpg','https://images.sbito.it/a.jpg',
 'https://cdn.wallapop.com/a.jpg','https://prod.pictures.autoscout24.net/a.jpg'])
def test_state_exposes_safe_photo(gui,url):
    populate(url)
    assert json.loads(request(gui,'/api/state')[1])['listings'][0]['image_url']==url
    csp=request(gui,'/')[2]['Content-Security-Policy']
    assert 'https://*.vinted.net' in csp


@pytest.mark.parametrize('url', [None,'javascript:alert(1)','http://images1.vinted.net/a.jpg',
 'https://127.0.0.1/a.jpg','https://user:password@images1.vinted.net/a.jpg',
 'https://images1.vinted.net.evil.example/a.jpg','https://evil.example/a.jpg',
 'https://images1.vinted.net:9443/a.jpg'])
def test_state_rejects_untrusted_photo(gui,url):
    populate(url)
    assert json.loads(request(gui,'/api/state')[1])['listings'][0]['image_url'] is None


@pytest.mark.skipif(os.environ.get('MARKETWATCH_BROWSER')!='1',reason='explicit browser validation')
def test_dark_theme_photo_loading_and_broken_fallback(gui):
    from playwright.sync_api import sync_playwright,expect
    import struct
    import zlib
    populate('https://images1.vinted.net/photo.png')
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(b'\x00\x40\xa0\xa0')) + chunk(b'IEND', b''))
    with sync_playwright() as p:
        browser=p.firefox.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':844})
        page.route('https://images1.vinted.net/**',lambda route:route.fulfill(content_type='image/png',body=png))
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        photo=page.locator('.listing-image')
        expect(photo).to_be_visible()
        photo.scroll_into_view_if_needed()
        expect(photo).to_have_js_property('naturalWidth', 1, timeout=10000)
        assert photo.get_attribute('loading')=='lazy'
        assert photo.get_attribute('referrerpolicy')=='no-referrer'
        assert page.locator('html').evaluate('(e)=>getComputedStyle(e).colorScheme')=='dark'
        assert page.locator('body').evaluate('(e)=>getComputedStyle(e).backgroundColor')=='rgb(15, 23, 32)'
        page.get_by_role('button',name='Nuova ricerca',exact=True).first.click()
        assert page.locator('#watch-dialog').evaluate('(e)=>getComputedStyle(e).backgroundColor')=='rgb(23, 34, 46)'
        assert page.locator('#search-text').evaluate('(e)=>getComputedStyle(e).backgroundColor')=='rgb(15, 23, 32)'
        page.get_by_role('button',name='Annulla',exact=True).click()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.unroute('https://images1.vinted.net/**')
        page.route('https://images1.vinted.net/**',lambda route:route.abort())
        page.reload()
        expect(page.get_by_text('Foto non disponibile',exact=True)).to_be_visible()
        browser.close()
