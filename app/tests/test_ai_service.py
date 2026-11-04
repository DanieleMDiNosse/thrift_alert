"""AI vertical slice: real SQLite, injected inference and retrieved evidence."""

import json
import pytest
from subitoo.core import db


@pytest.fixture
def ai_conn(tmp_path):
    c = db.init_db(str(tmp_path / "ai.db"))
    c.execute(
        "INSERT INTO queries(name,site,search_json,filters_json,cron,status_changed_at,created_at,updated_at) VALUES ('test','subito','{}','{}','* * * * *',0,0,0)"
    )
    c.execute(
        "INSERT INTO listings(query_id,site_listing_id,title,last_price,currency,url,raw_json,first_seen_at,last_seen_at) VALUES (1,'abc','Acme Box 2',40,'EUR','https://example.com/item/abc','{}',1,1)"
    )
    yield c
    c.close()


def identity(**changes):
    return dict(
        category="speakers",
        manufacturer="Acme",
        name="Box",
        model="Box",
        generation="2",
        variant="standard",
        confidence=0.9,
        **changes,
    )


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete(self, task, context):
        self.calls.append((task, context))
        if task == "identity":
            return identity()
        if task == "research":
            sid = context["sources"][0]["id"]
            return {
                "criteria": {"sound_quality": {"score": 82, "source_ids": [sid]}},
                "strengths": ["Clear sound"],
                "weaknesses": [],
                "known_issues": [],
                "specifications": {"power": "20W"},
                "summary": "Evidence-based summary",
                "confidence": 0.8,
            }
        return {
            "message": "Consider the documented sound quality.",
            "source_ids": [s["id"] for p in context["products"] for s in p["sources"]],
        }


class FakeSearch:
    def search(self, query):
        return [{"url": "https://example.com/review", "title": "Review"}]

    def fetch_page(self, url):
        return {
            "url": url,
            "title": "Independent review",
            "text": "The Box 2 has clear sound and 20W power.",
            "retrieved_at": 123,
            "publication_date": None,
        }


def enable(c):
    from subitoo.ai.service import action

    return action(c, "/api/ai/settings", {"enabled": True})


def test_snapshot_disabled_sync_dedup_and_original_untouched(ai_conn):
    from subitoo.ai.service import snapshot, action

    assert snapshot(ai_conn)["settings"]["enabled"] is False
    with pytest.raises(ValueError):
        action(ai_conn, "/api/ai/sync", {})
    before = dict(ai_conn.execute("SELECT * FROM listings").fetchone())
    enable(ai_conn)
    assert action(ai_conn, "/api/ai/sync", {})["enqueued"] == 1
    assert action(ai_conn, "/api/ai/sync", {})["enqueued"] == 0
    assert len(snapshot(ai_conn)["jobs"]) == 1
    assert dict(ai_conn.execute("SELECT * FROM listings").fetchone()) == before


def test_identification_research_chat_and_refresh(ai_conn):
    from subitoo.ai import service
    from subitoo.ai.worker import Worker

    enable(ai_conn)
    service.action(ai_conn, "/api/ai/sync", {})
    llm = FakeLLM()
    worker = Worker(ai_conn, llm=llm, search=FakeSearch())
    assert worker.run_once()
    state = service.snapshot(ai_conn)
    assert len(state["products"]) == 1
    product = state["products"][0]
    pid = product["id"]
    assert state["listings"][0]["product_id"] == pid
    assert worker.run_once()
    product = service.snapshot(ai_conn)["products"][0]
    assert product["research_status"] == "ready"
    assert product["research"]["criteria"]["sound_quality"]["score"] == 82
    assert product["sources"][0]["retrieved_at"] == 123
    cid = service.action(ai_conn, "/api/ai/conversations/new", {"product_ids": [pid]})[
        "id"
    ]
    result = service.action(
        ai_conn,
        f"/api/ai/conversations/{cid}/messages",
        {"message": "Which one?", "research": False},
    )
    assert result["status"] == "queued"
    assert len(service.snapshot(ai_conn)["conversations"][0]["messages"]) == 1
    assert worker.run_once()
    messages = service.snapshot(ai_conn)["conversations"][0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["source_ids"] == [product["sources"][0]["id"]]
    service.action(ai_conn, f"/api/ai/conversations/{cid}/clear", {})
    assert service.snapshot(ai_conn)["conversations"][0]["messages"] == []
    assert (
        service.action(ai_conn, f"/api/ai/products/{pid}/refresh", {})["status"]
        == "queued"
    )


def test_unknown_variants_not_merged(ai_conn):
    from subitoo.ai import store

    one = identity()
    one["variant"] = None
    two = identity()
    two["variant"] = None
    p1 = store.save_identity(ai_conn, 1, one)
    ai_conn.execute(
        "INSERT INTO listings(query_id,site_listing_id,title,first_seen_at,last_seen_at) VALUES(1,'other','Acme Box 2',1,1)"
    )
    p2 = store.save_identity(ai_conn, 2, two)
    assert p1 != p2
    two["variant"] = "standard"
    p3 = store.save_identity(ai_conn, 2, two)
    assert store.save_identity(ai_conn, 1, two) == p3


def test_evidence_schema_rejects_invented_citations():
    from subitoo.ai.schemas import validate_research, validate_identity, validate_chat

    with pytest.raises(ValueError):
        validate_research(
            {
                "criteria": {"sound": {"score": 100, "source_ids": ["invented"]}},
                "strengths": [],
                "weaknesses": [],
                "known_issues": [],
                "specifications": {},
                "summary": "x",
                "confidence": 0.8,
            },
            {"real"},
        )
    with pytest.raises(ValueError):
        validate_identity({"category": "speakers", "confidence": 2})
    with pytest.raises(ValueError):
        validate_chat({"message": "x", "source_ids": ["bad"]}, set())


def test_failure_backoff_safe_errors_and_recovery(ai_conn):
    from subitoo.ai import service
    from subitoo.ai.worker import Worker

    class Broken:
        def complete(self, *a):
            raise RuntimeError("SECRET_TOKEN=unsafe")

    enable(ai_conn)
    service.action(ai_conn, "/api/ai/sync", {})
    worker = Worker(ai_conn, llm=Broken(), search=FakeSearch())
    assert worker.run_once()
    job = service.snapshot(ai_conn)["jobs"][0]
    assert job["status"] == "queued" and job["attempts"] == 1
    assert "SECRET" not in json.dumps(job)
    assert not worker.run_once()
    ai_conn.execute("UPDATE ai_jobs SET status='running',updated_at=0")
    worker.recover_stale()
    assert service.snapshot(ai_conn)["jobs"][0]["status"] == "queued"


def test_config_defaults_and_secret_redaction(monkeypatch):
    from subitoo.ai.config import AIConfig

    c = AIConfig(_env_file=None, api_key="secret")
    assert c.enabled is False and c.model == "underdog-saluki-27b-iq2"
    assert "secret" not in json.dumps(c.public())


def test_action_validation_and_clear_cancels_pending(ai_conn):
    from subitoo.ai.service import action, snapshot

    enable(ai_conn)
    with pytest.raises(ValueError):
        action(ai_conn, "/api/ai/settings", {"enabled": "false"})
    with pytest.raises(ValueError):
        action(ai_conn, "/api/ai/compare", {"product_ids": ["missing"]})
    cid = action(ai_conn, "/api/ai/conversations/new", {"product_ids": []})["id"]
    action(
        ai_conn,
        f"/api/ai/conversations/{cid}/messages",
        {"message": "hi", "research": False},
    )
    action(ai_conn, f"/api/ai/conversations/{cid}/clear", {})
    assert snapshot(ai_conn)["jobs"][0]["status"] == "cancelled"


def test_llm_http_json_validation_and_bounds(monkeypatch, tmp_path):
    from subitoo.ai.config import AIConfig
    from subitoo.ai.llm import LLMClient

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, n):
            return json.dumps(
                {"choices": [{"message": {"content": json.dumps(identity())}}]}
            ).encode()

    seen = []

    def urlopen(req, timeout):
        seen.append(json.loads(req.data))
        return Response()

    monkeypatch.setattr("subitoo.ai.llm._open_inference", urlopen)
    client = LLMClient(AIConfig(_env_file=None, lock_path=str(tmp_path / "llm.lock")))
    assert client.complete("identity", {"title": "Box"})["variant"] == "standard"
    assert seen[0]["model"] == "underdog-saluki-27b-iq2"
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["stream"] is False
