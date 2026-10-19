"""Description text is optional, literal, cached and fetched only on request."""
import json
import pytest
from subitoo.core import db
from subitoo.core.models import Filters, Listing
from tests.test_gui import gui, request


def listing(conn, *, site='vinted', raw=None):
    qid=db.create_query(conn,name='Descrizioni',site=site,search={},filters=Filters(),cron='*/30 * * * *')
    db.insert_listing(conn,qid,Listing(site_listing_id='321',title='Fotocamera',price=100,
        url='https://www.vinted.it/items/321-fotocamera',raw=raw or {}))


def test_native_search_description_available_without_requests(gui):
    conn=db.init_db(); listing(conn,site='subito',raw={'body':'Prima riga\nSeconda riga','private':'DO-NOT-EXPOSE'}); conn.close()
    state=json.loads(request(gui,'/api/state')[1])
    item=state['listings'][0]
    assert item['description']=='Prima riga\nSeconda riga'
    assert item['site_listing_id']=='321'
    assert not item['can_load_description']
    assert 'DO-NOT-EXPOSE' not in json.dumps(state)


def test_vinted_description_only_on_explicit_request_and_then_cached(gui,monkeypatch):
    from subitoo.core import descriptions
    conn=db.init_db(); listing(conn); conn.close()
    calls=[]
    def fetch(url,listing_id):
        calls.append((url,listing_id)); return 'Descrizione completa\nSeconda riga <b>testo letterale</b>'
    monkeypatch.setattr(descriptions,'fetch_vinted_description',fetch)
    item=json.loads(request(gui,'/api/state')[1])['listings'][0]
    assert item['description'] is None and item['can_load_description']
    assert calls==[]
    assert request(gui,'/api/listings/description',{'site':'vinted','id':'321'},csrf=False)[0]==403
    for _ in range(2):
        status,body,_=request(gui,'/api/listings/description',{'site':'vinted','id':'321'})
        assert status==200 and json.loads(body)['description'].startswith('Descrizione completa')
    assert len(calls)==1
    assert json.loads(request(gui,'/api/state')[1])['listings'][0]['description'].startswith('Descrizione completa')


def test_failed_fetch_does_not_create_empty_success_cache(gui,monkeypatch):
    from subitoo.core import descriptions
    conn=db.init_db(); listing(conn); conn.close()
    def fail(*args): raise RuntimeError('HTTP 403 private content must not leak')
    monkeypatch.setattr(descriptions,'fetch_vinted_description',fail)
    status,body,_=request(gui,'/api/listings/description',{'site':'vinted','id':'321'})
    assert status==503 and 'private content' not in body
    conn=db.init_db(); assert conn.execute('SELECT COUNT(*) FROM listing_descriptions').fetchone()[0]==0; conn.close()
    assert request(gui,'/api/listings/description',{'site':'vinted','id':'999'})[0]==404


def test_detail_follows_one_same_origin_item_redirect(monkeypatch):
    from types import SimpleNamespace
    from subitoo.core import descriptions
    payload="1:"+json.dumps({"item_id":321,"description":"Testo reale"})
    responses=[SimpleNamespace(status_code=307,headers={"location":"/items/321-fotocamera?referrer=catalog"}),
               SimpleNamespace(status_code=200,headers={"content-type":"text/x-component"},content=payload.encode(),text=payload)]
    calls=[]
    def get(url,**kwargs):
        calls.append(url); assert kwargs["allow_redirects"] is False
        return responses.pop(0)
    monkeypatch.setattr(descriptions,"build_context",lambda *a,**k:SimpleNamespace(http=SimpleNamespace(get=get)))
    assert descriptions.fetch_vinted_description("https://www.vinted.it/items/321-fotocamera","321")=="Testo reale"
    assert len(calls)==2


@pytest.mark.parametrize("destination", ["https://evil.example/items/321", "/items/999-other", "/member/login"])
def test_detail_never_follows_cross_origin_wrong_item_or_login_redirect(monkeypatch,destination):
    from types import SimpleNamespace
    from subitoo.core import descriptions
    calls=[]
    def get(url,**kwargs):
        calls.append(url); return SimpleNamespace(status_code=307,headers={"location":destination})
    monkeypatch.setattr(descriptions,"build_context",lambda *a,**k:SimpleNamespace(http=SimpleNamespace(get=get)))
    with pytest.raises(RuntimeError):
        descriptions.fetch_vinted_description("https://www.vinted.it/items/321-fotocamera","321")
    assert len(calls)==1


def test_description_bound_to_requested_item_not_recommendations():
    from subitoo.core.descriptions import parse_vinted_description
    payload=['$', 'ItemPage',None,{'plugins':[{'data':{'item_id':999,'description':'Wrong item'}},
        {'data':{'item_id':321,'description':'Riga uno\nRiga due <script>literal</script>'}}]}]
    text='0:I["module"]\n1:'+json.dumps(payload)
    assert parse_vinted_description(text,'321')=='Riga uno\nRiga due <script>literal</script>'
    for bad in ['<html>challenge</html>', '1:'+json.dumps({'description':'Unrelated SEO'}), text]:
        with pytest.raises(RuntimeError): parse_vinted_description(bad,'123')


@pytest.mark.parametrize('url',['https://evil.example/items/321','https://user:password@www.vinted.it/items/321',
 'http://www.vinted.it/items/321','https://www.vinted.it/items/999-other'])
def test_description_fetch_rejects_unsafe_or_wrong_item_url(url):
    from subitoo.core.descriptions import fetch_vinted_description
    with pytest.raises(ValueError): fetch_vinted_description(url,'321')
