"""Required description terms: persistence, strict matching and conditional detail fetches."""
import json

import pytest

from subitoo.core import db, descriptions, pipeline, watches
from subitoo.core.filters import apply_filters
from subitoo.core.models import Filters
from subitoo.core.searches import build_watch
from tests._helpers import FakeSite, make_listing


def spec(**extra):
    return {'name': 'camera', 'title_include': ['sony'],
            'description_include': ['garanzia', 'come nuovo'],
            'sources': {'fake': {'search_url': 'https://example.com/search'}}, **extra}


@pytest.mark.parametrize('text,accepted', [
    ('GARANZIA residua\nCome Nuovo', True), ('garanzia', False),
    ('come nuovo', False), ('', False), (None, False), (12, False),
])
def test_description_all_terms_case_insensitive_multiline(conn, text, accepted):
    wid = watches.save(conn, spec())
    q = watches.queries(conn, wid)[0]
    item = make_listing('one', title='sony garanzia come nuovo')
    item.raw = {'description': text}
    assert bool(apply_filters([item], q.filters)) is accepted


def test_literal_terms_and_subito_body(conn):
    wid = watches.save(conn, spec(description_include=['[ok]', 'a+b']))
    q = watches.queries(conn, wid)[0]
    yes = make_listing('yes', title='sony')
    yes.raw = {'body': 'a+b\n[OK]'}
    no = make_listing('no', title='sony')
    no.raw = {'body': 'aaab ok'}
    assert apply_filters([yes, no], q.filters) == [yes]


def test_quick_search_persists_description_and_edit_reseeds(conn):
    data = build_watch({'query': 'sony', 'description_include': ['garanzia'],
                        'sources': {'subito': {'enabled': True}}})
    wid = watches.save(conn, data)
    q = watches.queries(conn, wid)[0]
    assert watches.get(conn, wid)['description_include'] == ['garanzia']
    db.update_query(conn, q.id, seeded=True)
    watches.save(conn, {**data, 'description_include': ['scontrino']}, watch_id=wid)
    updated = watches.queries(conn, wid)[0]
    assert updated.id != q.id and not updated.seeded
    assert db.get_query(conn, updated.id).filters.description_include
    with pytest.raises(ValueError):
        watches.save(conn, {**data, 'description_include': [' ']})


def test_no_description_filter_preserves_missing_descriptions():
    item = make_listing('missing')
    assert apply_filters([item], Filters()) == [item]


def vinted_query(conn, terms=None):
    terms = ['garanzia'] if terms is None else terms
    wid = watches.save(conn, {'name': 'sony', 'title_include': ['sony'],
                             'max_price_eur': 500, 'description_include': terms,
                             'sources': {'vinted': {'search_url': 'https://www.vinted.it/catalog?search_text=sony'}}})
    return watches.queries(conn, wid)[0]


def test_vinted_fetch_only_candidates_and_cache(conn, monkeypatch):
    q = vinted_query(conn)
    calls = []
    def fetch(url, identity):
        calls.append(identity)
        return 'GARANZIA residua'
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', fetch)
    items = [make_listing('123', title='sony', price=100),
             make_listing('124', title='canon', price=100),
             make_listing('125', title='sony', price=600)]
    for item in items:
        item.url = f'https://www.vinted.it/items/{item.site_listing_id}-camera'
    pauses = []
    result = pipeline._matches(conn, q, items, sleep=pauses.append)
    assert [x.site_listing_id for x in result] == ['123']
    assert result[0].raw['description'] == 'GARANZIA residua'
    assert calls == ['123']
    assert [x.site_listing_id for x in pipeline._matches(conn, q, items, sleep=pauses.append)] == ['123']
    assert calls == ['123']
    assert conn.execute('SELECT description FROM listing_descriptions').fetchone()[0] == 'GARANZIA residua'


def test_vinted_failure_not_cached_or_seeded(conn, monkeypatch, recorder):
    q = vinted_query(conn)
    item = make_listing('123', title='sony')
    item.url = 'https://www.vinted.it/items/123-camera'
    def fail(*args):
        raise RuntimeError('vinted: description request HTTP 403')
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', fail)
    monkeypatch.setattr(pipeline, 'fetch_query', lambda *a, **k: type('Scan', (), {'listings': [item]})())
    pipeline.run_query(conn, q.id, send=recorder, sleep=lambda _: None)
    updated = db.get_query(conn, q.id)
    assert not updated.seeded and updated.status.value == 'error'
    assert conn.execute('SELECT count(*) FROM listing_descriptions').fetchone()[0] == 0
    assert not recorder.sent


def test_monitor_applies_description_before_notifications(conn, recorder):
    wid = watches.save(conn, spec())
    q = watches.queries(conn, wid)[0]
    pipeline.run_query(conn, q.id, send=recorder, sleep=lambda _: None)
    yes = make_listing('yes', title='sony')
    yes.raw = {'description': 'come nuovo con garanzia'}
    no = make_listing('no', title='sony garanzia come nuovo')
    FakeSite.to_return = [yes, no]
    pipeline.run_query(conn, q.id, send=recorder, sleep=lambda _: None)
    assert len(recorder.sent) == 1
    assert db.get_listing(conn, q.id, 'yes') is not None
    assert db.get_listing(conn, q.id, 'no') is None


def test_http_save_readback(gui):  # noqa: F811 - imported pytest fixture
    from tests.test_gui import request
    payload = {'query': 'sony', 'enabled': False, 'description_include': ['garanzia']}
    assert request(gui, '/api/watches/quick-save', payload)[0] == 200
    saved = json.loads(request(gui, '/api/state')[1])['watches'][0]
    assert saved['description_include'] == ['garanzia']


from tests.test_gui import gui  # noqa: E402,F401


def test_dry_run_does_not_persist_description_cache(conn, monkeypatch):
    q = vinted_query(conn)
    item = make_listing('123', title='sony')
    item.url = 'https://www.vinted.it/items/123-camera'
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', lambda *a: 'garanzia')
    monkeypatch.setattr(pipeline, 'fetch_query', lambda *a, **k: type('Scan', (), {'listings': [item]})())
    assert len(pipeline.dry_run(conn, q.id)) == 1
    assert conn.execute('SELECT count(*) FROM listing_descriptions').fetchone()[0] == 0


def test_vinted_no_filter_never_fetches_detail(conn, monkeypatch):
    q = vinted_query(conn, terms=[])
    def unexpected(*a):
        pytest.fail('unexpected detail fetch')
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', unexpected)
    item = make_listing('123', title='sony')
    assert pipeline._matches(conn, q, [item]) == [item]


def test_cached_empty_description_rejects_without_refetch(conn, monkeypatch):
    q = vinted_query(conn)
    conn.execute('INSERT INTO listing_descriptions VALUES (?,?,?,?)', ('vinted', '123', '', 1))
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', lambda *a: pytest.fail('unexpected fetch'))
    assert pipeline._matches(conn, q, [make_listing('123', title='sony')]) == []
