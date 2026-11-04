"""Recovery policy for observed DNS/timeouts and server-imposed rate limits."""
import pytest
from subitoo.core import db, pipeline
from subitoo.core.models import Filters, QueryStatus
from tests._helpers import FakeSite


def query(conn, name='camera'):
    return db.create_query(conn, name=name, site='fake', search={}, filters=Filters(),
                           cron='*/30 * * * *', run_delay_seconds=0)


@pytest.mark.parametrize('message,code,retryable', [
    ('Error: Page.goto: NS_ERROR_UNKNOWN_HOST', 'network', True),
    ('DNSError: curl: (6) Could not resolve host', 'network', True),
    ('wallapop: request failed (DNSError)', 'network', True),
    ('Error: NS_ERROR_CONNECTION_REFUSED', 'network', True),
    ('Timeout: curl: (28) Operation timed out after 120002 milliseconds with 0 bytes received', 'timeout', True),
    ('TimeoutError: Page.goto: Timeout 120000ms exceeded.', 'timeout', True),
    ('RuntimeError: vinted: description request HTTP 429', 'rate_limited', True),
    ('vinted: HTTP 503', 'temporary_server', True),
    ('vinted: HTTP 403', 'access_denied', False),
    ('CAPTCHA required', 'access_denied', False),
    ('TargetClosedError: Page.wait_for_timeout: Target page, context or browser has been closed', 'browser_closed', True),
    ('vinted: malformed item wrapper', 'invalid_response', False),
    ('ValueError: invalid URL', 'fetch_error', True),
])
def test_original_error_classification(message, code, retryable):
    from subitoo.core.recovery import classify
    failure = classify(RuntimeError(message))
    assert (failure.code, failure.retryable) == (code, retryable)


def test_dns_retries_after_persisted_backoff_without_reseeding(conn, monkeypatch, recorder):
    from subitoo.core import recovery
    qid = query(conn)
    db.update_query(conn, qid, seeded=True)
    monkeypatch.setattr(FakeSite, 'fetch', lambda *a: (_ for _ in ()).throw(RuntimeError('NS_ERROR_UNKNOWN_HOST')))
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder, sleep=lambda _: None)
    saved = recovery.snapshot(conn, qid)
    assert saved['error_code'] == 'network' and saved['retry_at'] >= 1300
    assert db.get_query(conn, qid).seeded
    before = conn.execute('SELECT count(*) FROM runs').fetchone()[0]
    assert pipeline.run_due(conn, now_ts=saved['retry_at']-1, send=recorder, sleep=lambda _: None) == []
    monkeypatch.setattr(FakeSite, 'fetch', lambda *a: [])
    assert pipeline.run_due(conn, now_ts=saved['retry_at'], send=recorder, sleep=lambda _: None) == [qid]
    assert conn.execute('SELECT count(*) FROM runs').fetchone()[0] == before+1
    assert recovery.snapshot(conn, qid) is None
    assert db.get_query(conn, qid).seeded and not recorder.sent


def test_rate_limit_cools_whole_site_and_honors_retry_after(conn, recorder, monkeypatch):
    from subitoo.core import recovery
    one, two = query(conn, 'one'), query(conn, 'two')
    calls = []
    def limited(*a):
        calls.append(1)
        raise recovery.HTTPFailure('fake', 429, retry_after=7200)
    monkeypatch.setattr(FakeSite, 'fetch', limited)
    assert pipeline.run_due(conn, now_ts=1000, send=recorder, sleep=lambda _: None) == [one]
    assert len(calls) == 1
    assert recovery.snapshot(conn, one)['retry_at'] >= 8200
    assert recovery.site_retry_at(conn, 'fake') >= 8200
    assert db.get_query(conn, two).status == QueryStatus.PENDING
    with pytest.raises(ValueError, match='pausa|cooldown'):
        recovery.manual_retry(conn, one, now_ts=1100)


def test_access_denial_and_schema_failures_not_auto_retried(conn, recorder, monkeypatch):
    from subitoo.core import recovery
    qid = query(conn)
    monkeypatch.setattr(FakeSite, 'fetch', lambda *a: (_ for _ in ()).throw(RuntimeError('HTTP 403')))
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder)
    assert recovery.snapshot(conn, qid)['retry_at'] is None
    assert pipeline.run_due(conn, now_ts=100000, send=recorder) == []


def test_retry_delay_grows_and_survives_failed_attempts(conn):
    from subitoo.core import recovery
    qid = query(conn)
    first = recovery.record_failure(conn, qid, RuntimeError('NS_ERROR_UNKNOWN_HOST'), now_ts=1000)
    second = recovery.record_failure(conn, qid, RuntimeError('NS_ERROR_UNKNOWN_HOST'), now_ts=2000)
    assert second['failure_count'] == 2
    assert second['retry_at']-2000 > first['retry_at']-1000
    for i in range(30):
        last = recovery.record_failure(conn, qid, RuntimeError('NS_ERROR_UNKNOWN_HOST'), now_ts=3000)
    assert last['retry_at']-3000 <= 21600


def test_legacy_transient_errors_recovered_but_denials_held(conn, monkeypatch, recorder):
    transient, denied = query(conn, 'old network'), query(conn, 'old denied')
    for qid, error in ((transient, 'Error: NS_ERROR_UNKNOWN_HOST'), (denied, 'HTTP 403')):
        db.set_status(conn, qid, QueryStatus.ERROR, ts=1000)
        rid = db.start_run(conn, qid, 1000)
        db.finish_run(conn, rid, status='error', error_message=error, ts=1000)
    monkeypatch.setattr(FakeSite, 'fetch', lambda *a: [])
    pipeline.run_due(conn, now_ts=10000, send=recorder, sleep=lambda _: None)
    assert db.get_query(conn, transient).status == QueryStatus.PENDING
    assert db.get_query(conn, denied).status == QueryStatus.ERROR


def test_stop_between_sources_does_not_claim_next(conn, recorder):
    one, two = query(conn, 'one'), query(conn, 'two')
    def stop():
        return db.get_query(conn, one).seeded
    assert pipeline.run_due(conn, now_ts=1000, send=recorder, should_stop=stop) == [one]
    assert not db.get_query(conn, two).seeded


def test_failure_finish_timestamp_reports_elapsed_time(conn, monkeypatch, recorder):
    qid = query(conn)
    times = iter([10.0, 35.0])
    monkeypatch.setattr(pipeline.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(FakeSite, 'fetch', lambda *a: (_ for _ in ()).throw(RuntimeError('NS_ERROR_UNKNOWN_HOST')))
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder)
    row = db.recent_runs(conn, qid)[0]
    assert row['finished_at']-row['started_at'] == 25


def test_crashed_scan_gets_closed_and_recovered_without_reseeding(conn, recorder):
    from subitoo.core import recovery
    qid = query(conn)
    db.update_query(conn, qid, seeded=True)
    db.set_status(conn, qid, QueryStatus.RUNNING, ts=1000)
    rid = db.start_run(conn, qid, 1000)
    pipeline.run_due(conn, now_ts=3000, send=recorder)
    row = conn.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
    assert row['status'] == 'error' and row['finished_at'] == 3000
    state = recovery.snapshot(conn, qid)
    assert state['retry_at'] == 3300
    pipeline.run_due(conn, now_ts=3300, send=recorder, sleep=lambda _: None)
    assert db.get_query(conn, qid).seeded
    assert db.get_query(conn, qid).status == QueryStatus.PENDING


def test_direct_call_under_site_cooldown_does_not_leave_claim_running(conn, recorder):
    qid = query(conn)
    assert db.claim_query(conn, qid, ts=1000)
    conn.execute('INSERT INTO site_backoff VALUES (?,?)', ('fake', 5000))
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder)
    assert db.get_query(conn, qid).status == QueryStatus.PENDING
    assert not conn.execute('SELECT * FROM runs').fetchall()
