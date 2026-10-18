"""Real HTTP tests against a loopback server and isolated database."""
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from typer.testing import CliRunner

from subitoo.config import Settings
from subitoo.core import db
from subitoo.core.models import Filters, QueryStatus
from subitoo.cli import app


@pytest.fixture
def gui(tmp_path, monkeypatch):
    import subitoo.config as config
    from subitoo.gui import make_server
    monkeypatch.setattr(config, '_settings', Settings(db_path=str(tmp_path/'gui.db')))
    monkeypatch.setenv('NTFY_TOPIC', 'private-test-topic-never-in-response')
    server = make_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(server, path, data=None, *, csrf=True, headers=None, raw=None):
    url = f'http://127.0.0.1:{server.server_port}{path}'
    h = dict(headers or {})
    if data is not None or raw is not None:
        h.setdefault('Content-Type', 'application/json')
        if csrf:
            h['X-CSRF-Token'] = server.csrf_token
        body = json.dumps(data).encode() if raw is None else raw
    else:
        body = None
    req = Request(url, data=body, headers=h)
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as e:
        response = e
    with response:
        return response.status, response.read().decode(), response.headers


def spec():
    return {'name': 'Fotocamera', 'enabled': False, 'max_price_eur': 550,
            'title_include': ['a6400'], 'sources': {
                'subito': {'search_url': 'https://www.subito.it/annunci-italia/vendita/usato/?q=sony'}}}


def test_home_and_assets_are_local_private_and_not_cacheable(gui):
    status, body, headers = request(gui, '/')
    assert status == 200 and 'Le tue ricerche' in body and 'Nuova ricerca' in body
    assert gui.csrf_token in body
    assert 'private-test-topic' not in body
    assert headers['Cache-Control'] == 'no-store'
    assert "default-src 'self'" in headers['Content-Security-Policy']
    for asset in ['/static/app.js', '/static/style.css']:
        assert request(gui, asset)[0] == 200
    state = json.loads(request(gui, '/api/state')[1])
    assert state['watches'] == [] and state['listings'] == []
    assert not state['monitor_running']
    assert 'private-test-topic' not in json.dumps(state)


def test_crud_uses_real_watch_core(gui):
    status, body, _ = request(gui, '/api/watches/save', spec())
    assert status == 200
    wid = json.loads(body)['id']
    state = json.loads(request(gui, '/api/state')[1])
    watch = state['watches'][0]
    assert watch['id'] == wid and watch['name'] == 'Fotocamera'
    assert not watch['enabled'] and not watch['queries'][0]['seeded']
    assert request(gui, f'/api/watches/{wid}/toggle', {'enabled': True})[0] == 200
    changed = {**spec(), 'name': 'Nuovo nome', 'id': wid}
    assert request(gui, '/api/watches/save', changed)[0] == 200
    assert json.loads(request(gui, '/api/state')[1])['watches'][0]['name'] == 'Nuovo nome'
    assert request(gui, f'/api/watches/{wid}/remove', {})[0] == 200
    assert json.loads(request(gui, '/api/state')[1])['watches'] == []


def test_validation_and_missing_ids_do_not_write(gui):
    assert request(gui, '/api/watches/save', {**spec(), 'max_price_eur': -1})[0] == 400
    bad = spec()
    bad['sources']['subito']['search_url'] = 'https://evil.example/'
    assert request(gui, '/api/watches/save', bad)[0] == 400
    assert request(gui, '/api/watches/999/toggle', {'enabled': True})[0] == 404
    assert request(gui, '/api/watches/999/remove', {})[0] == 404
    assert request(gui, '/api/watches/save', raw=b'not json')[0] == 400
    assert request(gui, '/api/watches/save', raw=b'x'*70000)[0] == 413
    assert request(gui, '/api/state')[0] == 200
    assert json.loads(request(gui, '/api/state')[1])['watches'] == []


def test_host_origin_and_csrf_guards(gui):
    assert request(gui, '/api/watches/save', spec(), csrf=False)[0] == 403
    assert request(gui, '/api/watches/save', spec(), headers={'Origin': 'https://evil.example'})[0] == 403
    assert request(gui, '/', headers={'Host': 'evil.example'})[0] == 403
    assert request(gui, '/api/state', headers={'Origin': 'https://evil.example'})[0] == 403
    assert request(gui, '/api/watches/save', spec(), headers={'Content-Type':'text/plain'})[0] == 415
    assert request(gui, '/not-found')[0] == 404
    assert request(gui, '/static/../../.env')[0] == 404


def test_retry_only_resets_error_and_listings_are_safe(gui):
    conn = db.init_db()
    qid = db.create_query(conn, name='Standalone', site='fake', search={}, filters=Filters(), cron='*/30 * * * *')
    db.set_status(conn, qid, QueryStatus.ERROR)
    from subitoo.core.models import Listing
    db.insert_listing(conn, qid, Listing(site_listing_id='X', title='<script>bad</script>', price=5,
                                        url='javascript:alert(1)', raw={'secret':'RAW-DO-NOT-EXPOSE'}))
    conn.close()
    state = json.loads(request(gui, '/api/state')[1])
    assert state['listings'][0]['url'] is None
    assert 'RAW-DO-NOT-EXPOSE' not in json.dumps(state)
    assert request(gui, f'/api/queries/{qid}/retry', {})[0] == 200
    conn = db.init_db()
    assert db.get_query(conn, qid).status == QueryStatus.PENDING
    conn.close()


def test_invalid_structured_inputs_are_client_errors(gui):
    for source in ([], None, "bad"):
        assert request(gui, "/api/watches/save", {**spec(), "sources":source})[0] == 400
    assert request(gui, "/api/watches/save", {**spec(), "id": True})[0] == 400
    assert request(gui, "/api/watches/save", {**spec(), "id": 999})[0] == 404
    assert request(gui, "/api/watches/save", spec(), headers={"Sec-Fetch-Site":"cross-site"})[0] == 403


def test_notify_test_is_explicit_rate_limited_and_does_not_expose_secrets(gui, monkeypatch):
    from subitoo.core import notify
    sent = []
    monkeypatch.setattr(notify, "send", sent.append)
    assert request(gui, "/api/notify/test", {}, csrf=False)[0] == 403
    assert sent == []
    assert request(gui, "/api/notify/test", {})[0] == 200
    assert len(sent) == 1 and "prova" in sent[0].title
    assert request(gui, "/api/notify/test", {})[0] == 400
    assert len(sent) == 1


def test_state_read_is_consistent_when_watch_deleted_concurrently(gui, monkeypatch):
    from subitoo.core import watches
    status, body, _ = request(gui, "/api/watches/save", spec())
    assert status == 200
    wid = json.loads(body)["id"]
    original = watches.list_all
    deleted = False
    def snapshot_then_delete(conn):
        nonlocal deleted
        result = original(conn)
        if not deleted:
            other = db.init_db()
            watches.remove(other, wid)
            other.close()
            deleted = True
        return result
    monkeypatch.setattr(watches, "list_all", snapshot_then_delete)
    status, body, _ = request(gui, "/api/state")
    assert status == 200
    state = json.loads(body)
    assert len(state["watches"]) == 1 and len(state["watches"][0]["queries"]) == 1
    assert json.loads(request(gui, "/api/state")[1])["watches"] == []


@pytest.mark.parametrize("status,seeded", [(QueryStatus.PENDING,False), (QueryStatus.PENDING,True), (QueryStatus.RUNNING,True)])
def test_retry_does_not_modify_pending_running_or_completed_queries(gui, status, seeded):
    conn = db.init_db()
    qid = db.create_query(conn, name="Non in errore", site="fake", search={}, filters=Filters(), cron="*/30 * * * *")
    db.update_query(conn, qid, seeded=seeded)
    db.set_status(conn, qid, status, ts=123)
    before = dict(conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone())
    assert request(gui, f"/api/queries/{qid}/retry", {})[0] == 200
    after = dict(conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone())
    assert before == after
    conn.close()


def test_server_rejects_remote_bind():
    from subitoo.gui import make_server
    with pytest.raises(ValueError, match='127.0.0.1'):
        make_server(host='0.0.0.0', port=0)


def test_gui_command_is_discoverable():
    result = CliRunner().invoke(app, ['gui', '--help'])
    assert result.exit_code == 0 and '8787' in result.output
