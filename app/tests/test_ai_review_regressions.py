# ruff: noqa: F811
"""Regressions for independently reproduced review findings."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from tests.test_ai_service import ai_conn, enable, identity, FakeLLM, FakeSearch  # noqa: F401
from tests.test_ai_ranking import product, listing
from subitoo.ai import service, store, ranking
from subitoo.ai.config import AIConfig
from subitoo.ai.llm import LLMClient, InferenceError
from subitoo.ai.worker import Worker


def test_bearer_never_forwarded_to_redirect_target(tmp_path):
    received = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{target.server_port}/capture"
            )
            self.end_headers()

        def log_message(self, *args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = []
    for server in [target, origin]:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        threads.append(t)
    try:
        cfg = AIConfig(
            _env_file=None,
            endpoint=f"http://127.0.0.1:{origin.server_port}/v1",
            api_key="synthetic-test-not-a-real-credential",
            retries=0,
            timeout=2,
            lock_path=str(tmp_path / "infer.lock"),
        )
        with pytest.raises(InferenceError):
            LLMClient(cfg).complete("identity", {"title": "Acme"})
        assert received == []
    finally:
        for server in [origin, target]:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_full_queue_replaces_identification_slot_with_research(ai_conn):
    enable(ai_conn)
    cfg = AIConfig(_env_file=None, max_pending_jobs=1, max_attempts=1)
    store.enqueue(ai_conn, "identify", 1, config=cfg)
    worker = Worker(ai_conn, config=cfg, llm=FakeLLM(), search=FakeSearch())
    assert worker.run_once()
    assert len(store.products(ai_conn)) == 1
    jobs = store.jobs(ai_conn)
    assert {j["kind"]: j["status"] for j in jobs} == {
        "identify": "done",
        "research": "queued",
    }


def test_long_chat_budget_preserves_newest_question_and_history(ai_conn):
    enable(ai_conn)
    cid = service.action(ai_conn, "/api/ai/conversations/new", {"product_ids": []})[
        "id"
    ]
    cfg = AIConfig(_env_file=None, max_context_chars=12000)

    class Capture(FakeLLM):
        def complete(self, task, context):
            assert len(store.dumps(context)) <= cfg.max_context_chars
            assert context["messages"][-1]["content"] == "Newest question " + (
                "b" * 7900
            )
            assert all(set(m) == {"role", "content"} for m in context["messages"])
            return {"message": "Answer", "source_ids": []}

    for _ in range(8):
        ai_conn.execute(
            "INSERT INTO ai_messages(conversation_id,role,message,created_at) VALUES(?,'assistant',?,0)",
            (cid, "Old answer " + ("a" * 15900)),
        )
    service.action(
        ai_conn,
        f"/api/ai/conversations/{cid}/messages",
        {"message": "Newest question " + ("b" * 7900)},
    )
    assert Worker(ai_conn, config=cfg, llm=Capture()).run_once()
    assert store.messages(ai_conn, cid)[-1]["role"] == "assistant"
    assert len(store.messages(ai_conn, cid)) == 10


def test_hypothetical_comparables_never_change_observed_market():
    ps = [product()]
    ls = [listing(1, 50)] + [listing(i, 100) for i in range(2, 7)]
    observed = ranking.rank(ps, ls)["listings"][0]
    scenario = ranking.rank(ps, ls, hypothetical={str(i): 1000 for i in range(2, 7)})[
        "listings"
    ][0]
    assert scenario["market"] == observed["market"]
    assert scenario["deal_score"] == observed["deal_score"] == 50


def test_facts_follow_marketplace_identity_across_duplicate_rows(ai_conn):
    enable(ai_conn)
    store.save_identity(ai_conn, 1, identity())
    service.action(
        ai_conn,
        "/api/ai/listings/1/facts",
        {"condition": "good", "shipping_cost": 0, "fees": 0},
    )
    ai_conn.execute(
        "INSERT INTO queries(name,site,search_json,filters_json,cron,status_changed_at,created_at,updated_at) VALUES ('overlap','subito','{}','{}','* * * * *',0,0,0)"
    )
    ai_conn.execute(
        "INSERT INTO listings(query_id,site_listing_id,title,last_price,first_seen_at,last_seen_at) VALUES(2,'abc','Acme Box',45,1,1000)"
    )
    items = store.listings(ai_conn)
    assert len(items) == 1 and items[0]["id"] == 2
    assert (
        items[0]["condition"] == "good"
        and items[0]["shipping_cost"] == 0
        and items[0]["fees"] == 0
    )
    assert items[0]["product_id"] == store.products(ai_conn)[0]["id"]


def test_extreme_finite_weights_are_scale_invariant_and_json_safe():
    ps = [product()]
    weights = {"camera": {key: 1e308 for key in ranking.DEFAULT_WEIGHTS["camera"]}}
    result = ranking.rank(ps, [listing(1)], weights=weights)
    assert result["quality"][0]["quality_score"] == pytest.approx(80)
    json.dumps(result, allow_nan=False)
    assert (
        result["quality"]
        == ranking.rank(
            ps,
            [listing(1)],
            weights={"camera": {key: 1 for key in ranking.DEFAULT_WEIGHTS["camera"]}},
        )["quality"]
    )


def test_discovery_fills_available_capacity_without_rolling_back_all(ai_conn):
    enable(ai_conn)
    ai_conn.execute(
        "INSERT INTO listings(query_id,site_listing_id,title,first_seen_at,last_seen_at) VALUES(1,'other','Acme Box',1,2)"
    )
    cfg = AIConfig(_env_file=None, max_pending_jobs=1)
    assert store.discover(ai_conn, config=cfg) == 1
    assert len(store.jobs(ai_conn)) == 1
    assert store.discover(ai_conn, config=cfg) == 0


@pytest.mark.parametrize("price", [1e308, 1e-320])
def test_extreme_prices_never_emit_nonfinite_scores(price):
    items = [listing(i, price, shipping_cost=price, fees=price) for i in range(7)]
    result = ranking.rank([product()], items)
    json.dumps(result, allow_nan=False)


def test_focused_research_keeps_existing_supported_assessment(ai_conn):
    enable(ai_conn)
    pid = store.save_identity(ai_conn, 1, identity())
    base = {
        "criteria": {"sound": {"score": 80, "source_ids": ["base"]}},
        "strengths": ["Clear sound"],
        "weaknesses": [],
        "known_issues": [],
        "specifications": {},
        "summary": "Original review",
        "confidence": 0.8,
    }
    evidence = [
        {
            "id": "base",
            "url": "https://example.org/base",
            "title": "Base review",
            "text": "Clear sound",
            "retrieved_at": store.now(),
        }
    ]
    store.save_research(ai_conn, pid, base, evidence)
    cid = service.action(ai_conn, "/api/ai/conversations/new", {"product_ids": [pid]})[
        "id"
    ]
    service.action(
        ai_conn,
        f"/api/ai/conversations/{cid}/messages",
        {"message": "Search Reddit for reliability", "research": True},
    )

    class Focused(FakeLLM):
        def complete(self, task, context):
            if task == "research":
                return {
                    "criteria": {},
                    "strengths": [],
                    "weaknesses": [],
                    "known_issues": [],
                    "specifications": {},
                    "summary": "No corroborated defect found in this source",
                    "confidence": 0.5,
                }
            return super().complete(task, context)

    assert Worker(ai_conn, llm=Focused(), search=FakeSearch()).run_once()
    result = store.products(ai_conn)[0]["research"]
    assert result["criteria"]["sound"]["score"] == 80
    assert result["criteria"]["sound"]["source_ids"] == ["base"]
