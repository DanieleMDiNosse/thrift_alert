"""Plain product text + EUR range must generate adapter-valid native searches."""
from urllib.parse import parse_qs, urlsplit
import pytest
from subitoo.core import watches, db
from subitoo.registry import get_site
from tests.test_gui import gui, request


def test_text_and_price_build_three_searches_without_urls(conn):
    from subitoo.core.searches import build_watch
    result = build_watch({'query': 'Sony a6400', 'min_price_eur':100, 'max_price_eur':550})
    assert result['name'] == 'Sony a6400' and result['search_text'] == 'Sony a6400'
    assert result['title_include'] == ['Sony', 'a6400']
    assert set(result['sources']) == {'subito','vinted','wallapop'}
    for site, source in result['sources'].items():
        get_site(site).validate_search({'url':source['search_url']})
        assert source['enabled'] and source['max_pages'] == 1
    urls = {site:parse_qs(urlsplit(source['search_url']).query) for site,source in result['sources'].items()}
    assert urls['subito']['q'] == ['Sony a6400']
    assert urls['vinted']['search_text'] == ['Sony a6400']
    assert urls['vinted']['order'] == ['newest_first']
    assert urls['wallapop']['keywords'] == ['Sony a6400']
    assert urls['wallapop']['order_by'] == ['newest']
    assert urls['wallapop']['min_sale_price'] == ['100.0']
    assert urls['wallapop']['max_sale_price'] == ['550.0']
    wid = watches.save(conn, result)
    assert len(watches.queries(conn, wid)) == 3
    assert all(not q.seeded and not q.notify_on_price_change for q in watches.queries(conn, wid))


@pytest.mark.parametrize('query', ['caffè & tè + 50% / "mini"', 'bici?', '山のカメラ'])
def test_query_encoding_roundtrips(query):
    from subitoo.core.searches import build_watch
    result = build_watch({'query':query})
    url = result['sources']['subito']['search_url']
    assert parse_qs(urlsplit(url).query)['q'] == [query]


@pytest.mark.parametrize('data', [{'query':'  '}, {'query':'x','min_price_eur':20,'max_price_eur':10},
 {'query':'x','max_price_eur':float('inf')}, {'query':'x','sources':{'facebook':{'enabled':True}}},
 {'query':'x','sources':{'subito':{'enabled':False}}}])
def test_invalid_quick_search_rejected(data):
    from subitoo.core.searches import build_watch
    with pytest.raises(ValueError):
        build_watch(data)


def test_quick_http_create_edit_keeps_shared_filters(gui):
    import json
    data={'query':'Sony a6400','min_price_eur':100,'max_price_eur':550,'enabled':False}
    status, body, _=request(gui,'/api/watches/quick-save',data)
    assert status==200,body
    wid=json.loads(body)['id']
    state=json.loads(request(gui,'/api/state')[1])['watches'][0]
    assert state['search_text']=='Sony a6400' and len(state['queries'])==3
    assert not state['enabled']
    status, body, _=request(gui,'/api/watches/quick-save',{**data,'id':wid,'query':'Sony a6700'})
    assert status==200,body
    conn=db.init_db()
    saved=watches.get(conn,wid)
    assert saved['search_text']=='Sony a6700' and saved['title_include']==['Sony','a6700']
    conn.close()
