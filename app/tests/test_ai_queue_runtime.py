# ruff: noqa: F811

from tests.test_ai_service import ai_conn, enable, identity, FakeLLM  # noqa: F401
from subitoo.ai import store, service
from subitoo.ai.worker import Worker
from subitoo.ai.llm import InferenceError


def test_summary_counts_all_jobs_but_details_bounded(ai_conn):
    for index in range(250):
        store.enqueue(ai_conn, "identify", 100 + index)
    state = service.snapshot(ai_conn)
    assert state["job_summary"]["queued"] == 250
    assert len(state["jobs"]) <= 10


def test_retry_available_while_paused_but_no_duplicate_running_job(ai_conn):
    jid = store.enqueue(ai_conn, "identify", 1)
    ai_conn.execute(
        "UPDATE ai_jobs SET status='failed',attempts=3,error='old' WHERE id=?", (jid,)
    )
    service.action(ai_conn, f"/api/ai/jobs/{jid}/retry", {})
    row = ai_conn.execute("SELECT * FROM ai_jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "queued" and row["attempts"] == 0 and row["error"] is None
    service.action(ai_conn, f"/api/ai/jobs/{jid}/retry", {})
    assert len(store.jobs(ai_conn)) == 1
    assert store.settings(ai_conn)["enabled"] is False


def test_identity_prompt_contains_only_safe_relevant_listing_text(ai_conn):
    enable(ai_conn)
    ai_conn.execute(
        "UPDATE listings SET raw_json=?",
        (
            '{"description":"Model Box 2, no cables","secret":"DO_NOT_SEND","seller":{"name":"PRIVATE_NAME"}}',
        ),
    )
    store.enqueue(ai_conn, "identify", 1)

    class Captured(FakeLLM):
        def complete(self, task, context):
            assert "raw" not in context
            assert context["description"] == "Model Box 2, no cables"
            assert "DO_NOT_SEND" not in store.dumps(
                context
            ) and "PRIVATE_NAME" not in store.dumps(context)
            return identity()

    Worker(ai_conn, llm=Captured()).run_once()
    assert len(store.products(ai_conn)) == 1


def test_worker_persists_safe_error_code_not_exception_input(ai_conn):
    enable(ai_conn)
    store.enqueue(ai_conn, "identify", 1)

    class Broken:
        def complete(self, *args):
            raise InferenceError("DO_NOT_EXPOSE", code="output_truncated")

    Worker(ai_conn, llm=Broken()).run_once()
    row = ai_conn.execute("SELECT * FROM ai_jobs").fetchone()
    assert (
        row["error_code"] == "output_truncated" and "DO_NOT_EXPOSE" not in row["error"]
    )


def test_discovery_reuses_exact_native_identity_without_inference(ai_conn):
    enable(ai_conn)
    pid = store.save_identity(ai_conn, 1, identity())
    ai_conn.execute(
        "INSERT INTO queries(name,site,search_json,filters_json,cron,status_changed_at,created_at,updated_at) VALUES('other','subito','{}','{}','* * * * *',0,0,0)"
    )
    ai_conn.execute(
        "INSERT INTO listings(query_id,site_listing_id,title,last_price,first_seen_at,last_seen_at) VALUES(2,'abc','Acme Box 2',40,1,2)"
    )
    store.discover(ai_conn)

    class Forbidden:
        def complete(self, *args):
            raise AssertionError("No extra inference for native duplicate")

    worker = Worker(ai_conn, llm=Forbidden())
    while ai_conn.execute(
        "SELECT 1 FROM ai_jobs WHERE kind='identify' AND status='queued'"
    ).fetchone():
        worker.run_once()
    assert (
        ai_conn.execute(
            "SELECT product_id FROM ai_listing_products WHERE listing_id=2"
        ).fetchone()[0]
        == pid
    )


def test_exclusive_worker_start_recovers_recent_interrupted_claim(ai_conn):
    enable(ai_conn)
    jid = store.enqueue(ai_conn, "identify", 1)
    ai_conn.execute("UPDATE ai_jobs SET status='running',attempts=3 WHERE id=?", (jid,))
    worker = Worker(ai_conn, llm=FakeLLM())
    worker.recover_stale(recover_all=True)
    row = ai_conn.execute(
        "SELECT status,attempts FROM ai_jobs WHERE id=?", (jid,)
    ).fetchone()
    assert row["status"] == "queued" and row["attempts"] == 2


def test_paused_retry_still_respects_queue_capacity(ai_conn, monkeypatch):
    from subitoo.ai.config import AIConfig
    import pytest

    cfg = AIConfig(_env_file=None, max_pending_jobs=1)
    monkeypatch.setattr("subitoo.ai.service.store.get_config", lambda: cfg)
    failed = store.enqueue(ai_conn, "identify", 1)
    ai_conn.execute("UPDATE ai_jobs SET status='failed' WHERE id=?", (failed,))
    store.enqueue(ai_conn, "identify", 2)
    with pytest.raises(ValueError):
        service.action(ai_conn, f"/api/ai/jobs/{failed}/retry", {})


def test_discovery_limits_new_identification_backlog(ai_conn):
    from subitoo.ai.config import AIConfig

    for index in range(20):
        ai_conn.execute(
            "INSERT INTO listings(query_id,site_listing_id,title,first_seen_at,last_seen_at) VALUES(1,?,'Acme Box',1,2)",
            (str(index),),
        )
    cfg = AIConfig(_env_file=None, identification_backlog=3)
    assert store.discover(ai_conn, config=cfg) == 3
    assert store.discover(ai_conn, config=cfg) == 0
    assert store.job_summary(ai_conn)["queued"] == 3


def test_selected_watch_analysis_has_a_bounded_priority_batch(ai_conn):
    enable(ai_conn)
    for index in range(8):
        ai_conn.execute(
            "INSERT INTO listings(query_id,site_listing_id,title,first_seen_at,last_seen_at) VALUES(1,?,'Acme Box',1,2)",
            (str(index),),
        )
    result = service.action(ai_conn, "/api/ai/sync", {"limit": 3, "watch_id": None})
    assert result["enqueued"] == 3 and result["prioritized"] == 3
    assert (
        ai_conn.execute("SELECT count(*) FROM ai_jobs WHERE priority=10").fetchone()[0]
        == 3
    )
