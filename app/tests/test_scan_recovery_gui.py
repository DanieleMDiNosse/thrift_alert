# ruff: noqa: F811
"""The dashboard distinguishes temporary recovery, rate limits and real denials."""
import json
import os

import pytest
from subitoo.core import db, recovery
from tests.test_gui import gui, request, spec  # noqa: F401


def failed_source(gui, error, *, delay=3600):
    wid = json.loads(request(gui, '/api/watches/save', {**spec(), 'enabled': True})[1])['id']
    c = db.init_db()
    qid = c.execute('SELECT query_id FROM watch_sources WHERE watch_id=?', (wid,)).fetchone()[0]
    now = db.now()
    rid = db.start_run(c, qid, now)
    db.finish_run(c, rid, status='error', error_message=error, ts=now)
    c.execute("UPDATE queries SET status='error' WHERE id=?", (qid,))
    recovery.record_failure(c, qid, error, now_ts=now)
    c.close()
    return qid


def test_rate_limit_is_not_access_denied_and_manual_retry_cannot_bypass(gui):
    qid = failed_source(gui, 'vinted: description request HTTP 429')
    query = json.loads(request(gui, '/api/state')[1])['watches'][0]['queries'][0]
    assert query['last_attempt']['error_code'] == 'rate_limited'
    assert query['recovery']['retry_at'] > db.now()
    assert query['site_retry_at'] > db.now()
    assert request(gui, f'/api/queries/{qid}/retry', {})[0] == 400


def test_closed_browser_method_name_is_not_timeout(gui):
    failed_source(gui, 'TargetClosedError: Page.wait_for_timeout: Target page, context or browser has been closed')
    query = json.loads(request(gui, '/api/state')[1])['watches'][0]['queries'][0]
    assert query['last_attempt']['error_code'] == 'browser_closed'


@pytest.mark.skipif(os.environ.get('MARKETWATCH_BROWSER') != '1', reason='explicit browser validation')
def test_browser_shows_rate_limit_resume_time_not_denial(gui):
    from playwright.sync_api import sync_playwright, expect
    failed_source(gui, 'vinted: description request HTTP 429')
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f'http://127.0.0.1:{gui.server_port}')
        expect(page.get_by_text('Limite richieste del mercatino: pausa temporanea.', exact=True)).to_be_visible()
        expect(page.get_by_text('Accesso negato dal mercatino.', exact=True)).to_have_count(0)
        expect(page.get_by_text('Ripresa automatica', exact=False)).to_be_visible()
        expect(page.get_by_role('button', name='Riprova scansione', exact=True)).to_be_disabled()
        browser.close()
