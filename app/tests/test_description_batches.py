"""Description fan-out is bounded and resumes the same catalog without data loss."""

from types import SimpleNamespace

from subitoo.core import db, descriptions, pipeline, recovery, watches
from subitoo.core.models import Listing
from subitoo.core.searches import build_watch
from subitoo.core.searching import QueryScan


def setup_scan(conn, monkeypatch, items):
    spec = build_watch({'query': 'sony', 'description_include': ['garanzia'],
                        'sources': {'vinted': {'enabled': True}}})
    wid = watches.save(conn, spec)
    qid = watches.queries(conn, wid)[0].id
    calls = []
    def scan(*a, **k):
        calls.append(1)
        return QueryScan(items, {'pages': 1, 'complete': True, 'reason': 'end', 'phrases': 1})
    monkeypatch.setattr(pipeline, 'fetch_query', scan)
    monkeypatch.setattr(pipeline, 'DESCRIPTION_BATCH_SIZE', 2)
    return qid, calls


def candidates(n):
    return [Listing(str(i), 'sony', 50, url=f'https://www.vinted.it/items/{i}-sony') for i in range(100, 100+n)]


def test_description_batches_resume_catalog_without_reseed_or_repeat_fetch(conn, monkeypatch, recorder):
    qid, catalog_calls = setup_scan(conn, monkeypatch, candidates(3))
    detail_calls, waits = [], []
    def detail(url, identity):
        detail_calls.append(identity)
        return 'garanzia'
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', detail)
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder, sleep=waits.append)
    assert detail_calls == ['100', '101']
    assert all(value >= 10 for value in waits)
    assert not db.get_query(conn, qid).seeded
    assert db.list_listings(conn, qid) == []
    state = recovery.snapshot(conn, qid)
    assert state['error_code'] == 'description_pending' and state['retry_at'] == 1300
    assert conn.execute('SELECT pending_count FROM description_scans').fetchone()[0] == 1
    pipeline.run_due(conn, now_ts=1300, send=recorder, sleep=lambda _: None)
    assert catalog_calls == [1]
    assert detail_calls == ['100', '101', '102']
    assert db.get_query(conn, qid).seeded and len(db.list_listings(conn, qid)) == 3
    assert not recorder.sent
    assert conn.execute('SELECT count(*) FROM description_scans').fetchone()[0] == 0


def test_rate_limit_mid_batch_retains_catalog_and_successful_cache(conn, monkeypatch, recorder):
    qid, catalog_calls = setup_scan(conn, monkeypatch, candidates(2))
    def detail(url, identity):
        if identity == '101':
            raise recovery.HTTPFailure('vinted', 429, retry_after=7200)
        return 'garanzia'
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', detail)
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder, sleep=lambda _: None)
    state = recovery.snapshot(conn, qid)
    assert state['error_code'] == 'rate_limited'
    assert conn.execute('SELECT count(*) FROM listing_descriptions').fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM description_scans').fetchone()[0] == 1
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', lambda *a: 'garanzia')
    pipeline.run_due(conn, now_ts=state['retry_at'], send=recorder, sleep=lambda _: None)
    assert catalog_calls == [1] and db.get_query(conn, qid).seeded
    assert len(db.list_listings(conn, qid)) == 2 and not recorder.sent


def test_description_http_429_preserves_retry_after(monkeypatch):
    response = SimpleNamespace(status_code=429, headers={'Retry-After': '7200'})
    monkeypatch.setattr(descriptions, 'build_context', lambda *a, **k: SimpleNamespace(http=SimpleNamespace(get=lambda *a, **k: response)))
    import pytest
    with pytest.raises(recovery.HTTPFailure) as caught:
        descriptions.fetch_vinted_description('https://www.vinted.it/items/123-camera', '123')
    assert caught.value.status == 429 and caught.value.retry_after == 7200


def test_manual_description_rate_limit_pauses_monitor_and_repeated_clicks(conn, monkeypatch):
    from tests.test_descriptions import listing
    import pytest
    listing(conn)
    calls = []
    def limited(*args):
        calls.append(1)
        raise recovery.HTTPFailure('vinted', 429, retry_after=7200)
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', limited)
    with pytest.raises(recovery.HTTPFailure):
        descriptions.load_description(conn, 'vinted', '321')
    assert recovery.site_retry_at(conn, 'vinted') >= db.now()+7199
    with pytest.raises(ValueError, match='pausa'):
        descriptions.load_description(conn, 'vinted', '321')
    assert len(calls) == 1


def test_dry_run_respects_site_rate_limit(conn, monkeypatch):
    import pytest
    qid, catalog_calls = setup_scan(conn, monkeypatch, candidates(1))
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', lambda *a: pytest.fail('unexpected network call'))
    conn.execute('INSERT INTO site_backoff VALUES (?,?)', ('vinted', db.now()+3600))
    with pytest.raises(ValueError, match='pausa'):
        pipeline.dry_run(conn, qid)
    assert not catalog_calls


def _detail(monkeypatch, behaviour):
    calls = []
    def detail(url, identity):
        calls.append(identity)
        result = behaviour(identity)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', detail)
    return calls


def test_one_unreadable_item_never_blocks_the_query(conn, monkeypatch, recorder):
    monkeypatch.setattr(pipeline, 'DESCRIPTION_BATCH_SIZE', 10)
    qid, _ = setup_scan(conn, monkeypatch, candidates(3))
    monkeypatch.setattr(pipeline, 'DESCRIPTION_BATCH_SIZE', 10)
    calls = _detail(monkeypatch, lambda i: descriptions.DescriptionUnavailable('parse', 'boom')
                    if i == '101' else 'garanzia')
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder, sleep=lambda _: None)
    q = db.get_query(conn, qid)
    assert q.status.value == 'pending' and q.seeded
    assert {l['site_listing_id'] for l in db.list_listings(conn, qid)} == {'100', '102'}
    failure = conn.execute('SELECT * FROM description_failures').fetchone()
    assert failure['site_listing_id'] == '101' and failure['attempts'] == 1 and failure['retry_at'] == 1000 + 3600
    assert conn.execute('SELECT description_skipped FROM scan_coverage').fetchone()[0] == 1
    # Not retried before its own backoff; retried after it, and joins the baseline silently.
    pipeline.run_query(conn, qid, now_ts=2000, send=recorder, sleep=lambda _: None)
    assert calls.count('101') == 1
    _detail(monkeypatch, lambda i: 'garanzia')
    pipeline.run_query(conn, qid, now_ts=1000 + 3600, send=recorder, sleep=lambda _: None)
    assert {l['site_listing_id'] for l in db.list_listings(conn, qid)} == {'100', '101', '102'}
    assert not recorder.sent
    assert conn.execute('SELECT count(*) FROM description_failures').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM description_deferred').fetchone()[0] == 0


def test_item_retries_are_bounded_and_gone_items_are_permanent(conn, monkeypatch, recorder):
    qid, _ = setup_scan(conn, monkeypatch, candidates(2))
    monkeypatch.setattr(pipeline, 'DESCRIPTION_BATCH_SIZE', 10)
    calls = _detail(monkeypatch, lambda i: descriptions.DescriptionUnavailable('gone', 'x') if i == '100'
                    else descriptions.DescriptionUnavailable('redirect', 'y') if i == '101' else 'garanzia')
    for ts in (1000, 1000 + 3600, 1000 + 3600 + 4 * 3600, 10 ** 7):
        pipeline.run_query(conn, qid, now_ts=ts, send=recorder, sleep=lambda _: None)
        assert db.get_query(conn, qid).status.value == 'pending'
    assert calls.count('100') == 1 and calls.count('101') == 3
    rows = {r['site_listing_id']: r['retry_at'] for r in conn.execute('SELECT * FROM description_failures')}
    assert rows == {'100': None, '101': None}


def test_new_item_verified_after_baseline_is_notified(conn, monkeypatch, recorder):
    items = candidates(1)
    qid, _ = setup_scan(conn, monkeypatch, items)
    _detail(monkeypatch, lambda i: 'garanzia')
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder, sleep=lambda _: None)
    items.append(Listing('200', 'sony', 50, url='https://www.vinted.it/items/200-sony'))
    _detail(monkeypatch, lambda i: descriptions.DescriptionUnavailable('parse', 'x'))
    pipeline.run_query(conn, qid, now_ts=2000, send=recorder, sleep=lambda _: None)
    assert not recorder.sent and db.get_query(conn, qid).status.value == 'pending'
    _detail(monkeypatch, lambda i: 'garanzia')
    pipeline.run_query(conn, qid, now_ts=2000 + 3600, send=recorder, sleep=lambda _: None)
    assert len(recorder.sent) == 1


def test_systemic_description_failure_fails_loudly_without_penalising_items(conn, monkeypatch, recorder):
    qid, _ = setup_scan(conn, monkeypatch, candidates(4))
    monkeypatch.setattr(pipeline, 'DESCRIPTION_BATCH_SIZE', 10)
    _detail(monkeypatch, lambda i: descriptions.DescriptionUnavailable('parse', 'no description bound'))
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder, sleep=lambda _: None)
    assert db.get_query(conn, qid).status.value == 'error'
    assert recovery.snapshot(conn, qid)['error_code'] == 'invalid_response'
    assert conn.execute('SELECT count(*) FROM description_failures').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM description_scans').fetchone()[0] == 1


def test_batch_reuses_run_context_and_prioritises_fresh_items_over_retries(conn, monkeypatch, recorder):
    items = candidates(2)
    qid, _ = setup_scan(conn, monkeypatch, items)
    # Item 100 failed once and is due for retry at ts=5000; item 101 is brand new.
    conn.execute("INSERT INTO description_failures VALUES ('vinted','100','parse',1,4000,1000)")
    monkeypatch.setattr(pipeline, 'DESCRIPTION_BATCH_SIZE', 1)
    seen_ctx = []
    calls = []
    def detail(url, identity, *, ctx=None):
        calls.append(identity)
        seen_ctx.append(ctx)
        return 'garanzia'
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', detail)
    pipeline.run_query(conn, qid, now_ts=5000, send=recorder, sleep=lambda _: None)
    # Fresh item 101 is fetched in the first batch before retry item 100, using the shared run ctx.
    assert calls == ['101']
    assert seen_ctx[0] is not None
    pipeline.run_query(conn, qid, now_ts=5300, send=recorder, sleep=lambda _: None)
    assert calls == ['101', '100']
    assert {l['site_listing_id'] for l in db.list_listings(conn, qid)} == {'100', '101'}
