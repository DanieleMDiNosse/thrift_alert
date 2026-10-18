"""Durable global new-event semantics, independent of scheduler scratchpads."""
from subitoo.core import db, pipeline
from subitoo.core.models import Filters
from tests._helpers import FakeSite, make_listing
from tests.test_pipeline import mkquery, seed
import pytest


def test_different_schedules_and_restart_send_once(tmp_path, recorder):
    path = str(tmp_path / 'state.db')
    c = db.init_db(path)
    q1 = mkquery(c, name='one', cron='*/15 * * * *')
    q2 = mkquery(c, name='two', cron='*/30 * * * *')
    seed(c, q1, [], recorder)
    seed(c, q2, [], recorder)
    FakeSite.to_return = [make_listing('new')]
    pipeline.run_query(c, q1, send=recorder)
    c.close()
    c = db.init_db(path)
    pipeline.run_query(c, q2, send=recorder)
    pipeline.run_query(c, q1, send=recorder)
    assert len(recorder.sent) == 1
    assert db.get_listing(c, q2, 'new') is not None
    c.close()


def test_baseline_elsewhere_does_not_mask_failed_notification(conn, recorder):
    first = mkquery(conn, name='first')
    seed(conn, first, [], recorder)
    FakeSite.to_return = [make_listing('new')]
    recorder.fail = True
    pipeline.run_query(conn, first, send=recorder)
    second = mkquery(conn, name='second')
    pipeline.run_query(conn, second, send=recorder)  # baseline must not consume pending event
    recorder.fail = False
    pipeline.run_query(conn, first, send=recorder)
    assert len(recorder.sent) == 1


def test_later_baselined_group_retries_pending_when_original_disabled(conn, recorder):
    first = mkquery(conn, name="original")
    seed(conn, first, [], recorder)
    FakeSite.to_return = [make_listing("X")]
    recorder.fail = True
    pipeline.run_query(conn, first, send=recorder)
    later = mkquery(conn, name="later")
    pipeline.run_query(conn, later, send=recorder)
    db.update_query(conn, first, enabled=False)
    recorder.fail = False
    pipeline.run_query(conn, later, send=recorder)
    pipeline.run_query(conn, later, send=recorder)
    assert len(recorder.sent) == 1
    assert recorder.sent[0].query_name == "later"
    assert conn.execute("SELECT state FROM listing_events WHERE site_listing_id='X'").fetchone()[0] == "sent"
    assert len(db.list_listings(conn, later)) == 1


def test_global_baseline_is_not_new_in_later_existing_query(conn, recorder):
    first = mkquery(conn, name='first')
    second = mkquery(conn, name='second')
    seed(conn, second, [], recorder)
    seed(conn, first, [make_listing('old')], recorder)
    FakeSite.to_return = [make_listing('old')]
    pipeline.run_query(conn, second, send=recorder)
    assert recorder.sent == []


def test_price_changes_off_by_default(conn, recorder):
    qid = mkquery(conn)
    seed(conn, qid, [make_listing('old', price=100)], recorder)
    FakeSite.to_return = [make_listing('old', price=90)]
    pipeline.run_query(conn, qid, send=recorder)
    assert recorder.sent == []
    assert db.get_listing(conn, qid, 'old')['last_price'] == 90


def test_seed_is_atomic_and_retryable(conn, recorder, monkeypatch):
    qid = mkquery(conn)
    FakeSite.to_return = [make_listing("one"), make_listing("two")]
    original = db.insert_listing
    def fail_second(c, query_id, listing, ts):
        if listing.site_listing_id == "two":
            raise RuntimeError("injected disk failure")
        return original(c, query_id, listing, ts)
    monkeypatch.setattr(db, "insert_listing", fail_second)
    pipeline.run_query(conn, qid, send=recorder)
    assert not db.get_query(conn, qid).seeded
    assert db.list_listings(conn, qid) == []
    monkeypatch.setattr(db, "insert_listing", original)
    pipeline.run_query(conn, qid, send=recorder)
    assert db.get_query(conn, qid).seeded
    assert recorder.sent == []


def test_failure_does_not_seed_and_other_site_continues(conn, recorder):
    bad = mkquery(conn, site='boom', name='bad')
    good = mkquery(conn, name='good')
    pipeline.run_due(conn, send=recorder)
    assert not db.get_query(conn, bad).seeded
    assert db.get_query(conn, good).seeded
