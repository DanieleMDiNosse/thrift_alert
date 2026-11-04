"""Alternative phrases (OR), native price filtering and scan coverage semantics."""
from urllib.parse import parse_qs,urlsplit
import json
import pytest
from subitoo.core import db,watches,pipeline
from subitoo.core.searches import build_watch
from subitoo.core.filters import apply_filters
from tests._helpers import make_listing


def test_phrase_lines_are_or_not_one_combined_query(conn):
    spec=build_watch({'query':'casse attive\ncassa attiva\ndiffusori attivi','min_price_eur':40,'max_price_eur':100})
    assert spec['search_phrases']==['casse attive','cassa attiva','diffusori attivi']
    assert spec['name']=='casse attive'
    wid=watches.save(conn,spec)
    queries=watches.queries(conn,wid)
    assert len(queries)==3
    for q in queries:
        assert len(q.search['urls'])==3
        assert q.search['price_min']==40 and q.search['price_max']==100
        values=[parse_qs(urlsplit(u).query) for u in q.search['urls']]
        key={'subito':'q','vinted':'search_text','wallapop':'keywords'}[q.site]
        assert [v[key][0] for v in values]==spec['search_phrases']
        items=[make_listing(str(i),title=title,price=70) for i,title in enumerate(
            ['Casse attive Trevi','Una cassa attiva Yamaha','Diffusori attivi Lonpoo','Casse passive'])]
        assert len(apply_filters(items,q.filters))==3


def test_phrase_extra_terms_and_exclusions_apply_to_every_alternative(conn):
    spec=build_watch({'query':'casse attive\ndiffusori attivi','title_include':['bluetooth'],'title_exclude':['guasto']})
    q=watches.queries(conn,watches.save(conn,spec))[0]
    items=[make_listing(str(i),title=t) for i,t in enumerate(['casse attive bluetooth','diffusori attivi bluetooth',
            'diffusori attivi','casse attive bluetooth guasto'])]
    assert [x.title for x in apply_filters(items,q.filters)]==['casse attive bluetooth','diffusori attivi bluetooth']


def test_phrase_bounds_and_casefold_dedup():
    spec=build_watch({'query':' casse attive \nCASSE ATTIVE\n diffusori attivi '})
    assert spec['search_phrases']==['casse attive','diffusori attivi']
    assert spec['name']=='casse attive'
    named=build_watch({'name':'Impianto Audio','query':'casse attive\ndiffusori attivi'})
    assert named['name']=='Impianto Audio'
    assert named['search_phrases']==['casse attive','diffusori attivi']
    many=build_watch({'query':'\n'.join([
        'trek checkpoint alr 4',
        'trek checkpoint alr 5',
        'trek checkpoint sl 5',
        'canyon grail al 6',
        'canyon grail cf 7',
        'specialized diverge e5',
    ])})
    assert len(many['search_phrases'])==6
    # Common prefixes collapse 6 model lines into 3 native marketplace URLs.
    assert len(many['sources']['vinted']['search_urls'])==3
    with pytest.raises(ValueError): build_watch({'query':'\n'.join(f'phrase {i}' for i in range(51))})


def test_multiple_native_searches_share_budget_dedup_and_truthful_coverage(conn,recorder,monkeypatch):
    from subitoo.core.searching import fetch_query
    spec=build_watch({'query':'casse attive\ncassa attiva','sources':{'subito':{'enabled':True}},'enabled':False})
    q=watches.queries(conn,watches.save(conn,spec))[0]
    calls=[]
    class Results(list):
        coverage={'pages':2,'complete':True,'reason':'end'}
    class Adapter:
        def fetch(self,search,ctx):
            calls.append(search)
            return Results([make_listing('same',title='cassa attiva')])
    result=fetch_query(conn,q,Adapter(),None,sleep=lambda _:None)
    assert len(calls)==2 and len(result.listings)==1
    assert sum(int(c['max_pages']) for c in calls)<=30
    assert result.coverage['complete'] and result.coverage['phrases']==2
    assert result.coverage['pages']==4


def test_error_in_later_phrase_does_not_seed_partial_success(conn,recorder,monkeypatch):
    spec=build_watch({'query':'casse attive\ncassa attiva','sources':{'subito':{'enabled':True}},'enabled':False})
    q=watches.queries(conn,watches.save(conn,spec))[0]
    calls=[]
    class Adapter:
        needs_browser=False
        def fetch(self,search,ctx):
            calls.append(search)
            if len(calls)==2: raise RuntimeError('HTTP 429')
            return [make_listing('X',title='casse attive')]
    monkeypatch.setattr(pipeline,'get_site',lambda _:Adapter())
    pipeline.run_query(conn,q.id,send=recorder,sleep=lambda _:None)
    assert not db.get_query(conn,q.id).seeded
    assert db.list_listings(conn,q.id)==[]
    assert recorder.sent==[]


def test_partial_scan_exposed_as_partial_never_complete(conn,recorder,monkeypatch):
    spec=build_watch({'query':'casse attive','sources':{'subito':{'enabled':True}},'enabled':False})
    q=watches.queries(conn,watches.save(conn,spec))[0]
    class Results(list):
        coverage={'pages':30,'complete':False,'reason':'page_limit'}
    class Adapter:
        needs_browser=False
        def fetch(self,search,ctx): return Results([])
    monkeypatch.setattr(pipeline,'get_site',lambda _:Adapter())
    pipeline.run_query(conn,q.id,send=recorder,sleep=lambda _:None)
    row=conn.execute('SELECT coverage_json FROM scan_coverage WHERE query_id=?',(q.id,)).fetchone()
    coverage=json.loads(row[0])
    assert not coverage['complete'] and coverage['reason']=='page_limit'


def test_first_expanded_scan_of_old_quick_watch_is_silent(conn,recorder,monkeypatch):
    spec=build_watch({'query':'casse attive','sources':{'subito':{'enabled':True}},'enabled':False})
    q=watches.queries(conn,watches.save(conn,spec))[0]
    db.update_query(conn,q.id,seeded=True)
    db.insert_listing(conn,q.id,make_listing('old',title='casse attive'))
    class Adapter:
        needs_browser=False
        def fetch(self,search,ctx): return [make_listing('old',title='casse attive'),make_listing('backlog',title='casse attive')]
    monkeypatch.setattr(pipeline,'get_site',lambda _:Adapter())
    pipeline.run_query(conn,q.id,send=recorder,sleep=lambda _:None)
    assert recorder.sent==[]
    assert len(db.list_listings(conn,q.id))==2


def test_expanded_baseline_refreshes_price_without_delayed_alert(conn,recorder,monkeypatch):
    spec=build_watch({'query':'casse attive','sources':{'subito':{'enabled':True}},'enabled':False})
    q=watches.queries(conn,watches.save(conn,spec))[0]
    db.update_query(conn,q.id,seeded=True,notify_on_price_change=True)
    db.insert_listing(conn,q.id,make_listing('old',title='casse attive',price=80))
    class Adapter:
        needs_browser=False
        def fetch(self,search,ctx):
            return [make_listing('old',title='casse attive',price=60)]
    monkeypatch.setattr(pipeline,'get_site',lambda _:Adapter())
    pipeline.run_query(conn,q.id,now_ts=100,send=recorder,sleep=lambda _:None)
    assert recorder.sent==[]
    assert db.get_listing(conn,q.id,'old')['last_price']==60
    pipeline.run_query(conn,q.id,now_ts=200,send=recorder,sleep=lambda _:None)
    assert recorder.sent==[]


def test_incremental_pagination_stops_when_page_matches_are_already_known(conn,recorder,monkeypatch):
    from types import SimpleNamespace
    from subitoo.sites import vinted
    spec=build_watch({'query':'casse attive','sources':{'vinted':{'enabled':True}},'enabled':False})
    q=watches.queries(conn,watches.save(conn,spec))[0]
    pages_fetched=[]
    def page_payload(page_no,item_ids):
        data={'items':{'items':[{'productItem':{'id':i,'title':f'casse attive {i}','url':f'/items/{i}-casse','price':{'amount':'50.00','currencyCode':'EUR'}}} for i in item_ids]},
              'pagination':{'current_page':page_no,'total_pages':5,'per_page':2,'total_entries':10,'time':123}}
        return json.dumps(data,separators=(',',':'))
    def get(url,**kwargs):
        if url=='https://www.vinted.it/':
            return SimpleNamespace(status_code=200)
        assert kwargs.get('timeout')==30
        p=int(parse_qs(urlsplit(url).query)['page'][0])
        pages_fetched.append(p)
        if p==1:
            return SimpleNamespace(status_code=200,headers={},text=page_payload(1,['101','102']))
        return SimpleNamespace(status_code=200,headers={},text=page_payload(p,[f'{p}01',f'{p}02']))
    monkeypatch.setattr(pipeline,'build_context',lambda *a,**k:SimpleNamespace(http=SimpleNamespace(get=get)))
    monkeypatch.setattr(pipeline,'get_site',lambda _:vinted.VintedSite())
    monkeypatch.setattr(vinted.time,'sleep',lambda _:None)
    # First (seed) run fetches all 5 pages.
    pipeline.run_query(conn,q.id,now_ts=100,send=recorder,sleep=lambda _:None)
    assert pages_fetched==[1,2,3,4,5]
    # Second run sees page 1 items ('101','102') already in DB with unchanged price and stops after page 1.
    pages_fetched.clear()
    pipeline.run_query(conn,q.id,now_ts=200,send=recorder,sleep=lambda _:None)
    assert pages_fetched==[1]
    coverage=json.loads(conn.execute('SELECT coverage_json FROM scan_coverage WHERE query_id=?',(q.id,)).fetchone()[0])
    assert coverage['complete'] is True and coverage['pages']==1
