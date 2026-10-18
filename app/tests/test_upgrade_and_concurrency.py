"""Compatibility with the original schema, and serialized global delivery."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import sqlite3
import pytest

from subitoo.core import db, pipeline
from subitoo.core.models import Filters
from tests._helpers import FakeSite, make_listing
from tests.test_pipeline import mkquery, seed


def test_upgrade_preserves_legacy_setting_and_seen_events(tmp_path):
    c = db.connect(str(tmp_path/'old.db'))
    for version in (1, 2):
        path = next(p for n, p in db._migration_files() if n == version)
        c.executescript(path.read_text())
    c.execute('PRAGMA user_version=2')
    c.execute("INSERT INTO queries(name,site,search_json,filters_json,cron,status_changed_at,created_at,updated_at) VALUES ('legacy','fake','{}','{}','*/30 * * * *',1,1,1)")
    db.insert_listing(c, 1, make_listing('old'), ts=1)
    db.migrate(c)
    legacy = db.get_query(c, 1)
    assert legacy.notify_on_price_change
    new = db.create_query(c, name='new', site='fake', search={}, filters=Filters(), cron='*/30 * * * *')
    assert not db.get_query(c, new).notify_on_price_change
    assert c.execute("SELECT state FROM listing_events WHERE site='fake' AND site_listing_id='old'").fetchone()[0] == 'baseline'
    assert db.migrate(c) == 4
    c.close()


def test_interrupted_migration_rolls_back_and_can_retry(tmp_path):
    c = db.connect(str(tmp_path/"interrupted.db"))
    for version in (1, 2):
        path = next(p for n, p in db._migration_files() if n == version)
        c.executescript(path.read_text())
    c.execute("PRAGMA user_version=2")
    def deny_update(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_UPDATE and table == "queries" and column == "notify_on_price_change":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    c.set_authorizer(deny_update)
    with pytest.raises(sqlite3.DatabaseError):
        db.migrate(c)
    c.set_authorizer(None)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "notify_on_price_change" not in [r["name"] for r in c.execute("PRAGMA table_info(queries)")]
    assert not c.in_transaction
    c.close()
    c = db.init_db(str(tmp_path/"interrupted.db"))
    assert c.execute("PRAGMA user_version").fetchone()[0] == 4
    c.close()


def test_scheduler_does_not_claim_source_disabled_after_snapshot(conn, recorder, monkeypatch):
    qid = mkquery(conn)
    seed(conn, qid, [], recorder)
    FakeSite.to_return = [make_listing("new")]
    claim = db.claim_query
    def disable_then_claim(c, query_id, ts):
        db.update_query(c, query_id, enabled=False)
        return claim(c, query_id, ts)
    monkeypatch.setattr(db, "claim_query", disable_then_claim)
    assert pipeline.run_due(conn, now_ts=10000, send=recorder) == []
    assert recorder.sent == []
    assert not db.get_query(conn, qid).enabled


def test_simultaneous_separate_queries_send_once(tmp_path, recorder):
    path = str(tmp_path/'concurrent.db')
    c = db.init_db(path)
    ids = [mkquery(c, name=f'q{i}') for i in range(2)]
    for qid in ids:
        seed(c, qid, [], recorder)
    c.close()
    FakeSite.to_return = [make_listing('new')]
    sent = []
    barrier = threading.Barrier(2)
    def run(qid):
        conn = db.init_db(path)
        barrier.wait(timeout=5)
        result = pipeline.run_query(conn, qid, send=sent.append)
        assert db.get_query(conn, qid).status.value == 'pending'
        conn.close()
        return result.n_notified
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(run, ids))
    assert sum(counts) == 1 and len(sent) == 1
    c = db.init_db(path)
    assert all(db.get_listing(c, qid, 'new') is not None for qid in ids)
    c.close()
