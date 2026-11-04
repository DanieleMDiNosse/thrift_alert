# ruff: noqa: F811

from tests.test_gui import gui, request  # noqa: F401
from subitoo.core import db
from subitoo.core.models import Filters
import json


def test_failed_scan_keeps_previous_coverage_separate_from_latest_attempt(gui):
    c = db.init_db()
    qid = db.create_query(
        c,
        name="Coverage test",
        site="subito",
        search={},
        filters=Filters(),
        cron="*/30 * * * *",
    )
    # Standalone queries do not render watch status: bind through the real watch core.
    from tests.test_gui import spec

    wid = json.loads(request(gui, "/api/watches/save", spec())[1])["id"]
    qid = c.execute(
        "SELECT query_id FROM watch_sources WHERE watch_id=?", (wid,)
    ).fetchone()[0]
    c.execute(
        "INSERT INTO scan_coverage(query_id,coverage_json,scanned_at) VALUES(?,?,?)",
        (qid, '{"pages":27,"complete":false,"reason":"page_limit"}', 100),
    )
    c.execute(
        "INSERT INTO runs(query_id,started_at,finished_at,status,error_message) VALUES(?,200,260,'error','TimeoutError: secret private URL')",
        (qid,),
    )
    c.execute("UPDATE queries SET status='error' WHERE id=?", (qid,))
    c.close()
    value = json.loads(request(gui, "/api/state")[1])["watches"][0]["queries"][0]
    assert value["coverage_observed_at"] == 100
    assert value["last_attempt"] == {
        "status": "error",
        "started_at": 200,
        "finished_at": 260,
        "error_code": "timeout",
    }
    assert "secret private URL" not in json.dumps(value)
