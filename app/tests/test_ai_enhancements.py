# ruff: noqa: F811
# Imported fixtures are registered by pytest and used as function arguments.
import json
import pytest
from tests.test_ai_service import ai_conn, enable, identity, FakeLLM, FakeSearch  # noqa: F401
from subitoo.ai import service, store
from subitoo.ai.worker import Worker


def test_manual_facts_enable_cost_rankings_without_mutating_raw(ai_conn):
    enable(ai_conn)
    store.save_identity(ai_conn, 1, identity())
    before = dict(ai_conn.execute("SELECT * FROM listings").fetchone())
    service.action(
        ai_conn,
        "/api/ai/listings/1/facts",
        {
            "condition": "good",
            "shipping_cost": 5,
            "fees": 2,
            "accessories": ["power supply"],
            "warranty": None,
            "age_years": 2,
        },
    )
    item = service.snapshot(ai_conn)["listings"][0]
    assert (
        item["condition"] == "good" and item["shipping_cost"] == 5 and item["fees"] == 2
    )
    assert item["facts_provenance"] == "user supplied; verify with seller"
    assert dict(ai_conn.execute("SELECT * FROM listings").fetchone()) == before
    for data in [
        {"fees": True},
        {"fees": -1},
        {"condition": "perfect"},
        {"shipping_cost": float("nan")},
    ]:
        with pytest.raises(ValueError):
            service.action(ai_conn, "/api/ai/listings/1/facts", data)


def test_research_ttl_reuses_fresh_and_schedules_stale(ai_conn, monkeypatch):
    enable(ai_conn)
    pid = store.save_identity(ai_conn, 1, identity())
    research = {
        "criteria": {},
        "strengths": [],
        "weaknesses": [],
        "known_issues": [],
        "specifications": {},
        "summary": "cached",
        "confidence": 0.5,
    }
    evidence = [
        {
            "id": "s",
            "url": "https://example.org/review",
            "title": "Review",
            "text": "Evidence",
            "retrieved_at": store.now(),
        }
    ]
    store.save_research(ai_conn, pid, research, evidence)
    store.discover(ai_conn)
    assert not ai_conn.execute("SELECT 1 FROM ai_jobs WHERE kind='research'").fetchone()
    ai_conn.execute("UPDATE ai_products SET researched_at=1")
    assert store.products(ai_conn)[0]["research_status"] == "needs_refresh"
    store.discover(ai_conn)
    assert (
        ai_conn.execute(
            "SELECT count(*) FROM ai_jobs WHERE kind='research' AND status='queued'"
        ).fetchone()[0]
        == 1
    )
    store.discover(ai_conn)
    assert (
        ai_conn.execute(
            "SELECT count(*) FROM ai_jobs WHERE kind='research'"
        ).fetchone()[0]
        == 1
    )


def test_model_offline_remains_retryable_after_retry_budget(ai_conn):
    from subitoo.ai.llm import InferenceUnavailable
    from subitoo.ai.config import AIConfig

    class Offline:
        def complete(self, *args):
            raise InferenceUnavailable("offline")

    enable(ai_conn)
    store.discover(ai_conn)
    worker = Worker(
        ai_conn, config=AIConfig(_env_file=None, max_attempts=1), llm=Offline()
    )
    for _ in range(3):
        assert worker.run_once()
        row = ai_conn.execute("SELECT * FROM ai_jobs").fetchone()
        assert row["status"] == "queued"
        ai_conn.execute("UPDATE ai_jobs SET available_at=0")


def test_research_intent_passes_actual_question_to_search(ai_conn):
    enable(ai_conn)
    pid = store.save_identity(ai_conn, 1, identity())
    cid = service.action(ai_conn, "/api/ai/conversations/new", {"product_ids": [pid]})[
        "id"
    ]
    service.action(
        ai_conn,
        f"/api/ai/conversations/{cid}/messages",
        {"message": "Search Reddit for long-term reliability", "research": True},
    )

    class Search(FakeSearch):
        def __init__(self):
            self.queries = []

        def search(self, q):
            self.queries.append(q)
            return super().search(q)

    search = Search()
    worker = Worker(ai_conn, llm=FakeLLM(), search=search)
    assert worker.run_once()
    assert "Reddit" in search.queries[0] and "long-term" in search.queries[0]


def test_chat_survives_database_reopen(ai_conn):
    from subitoo.core import db

    enable(ai_conn)
    cid = service.action(ai_conn, "/api/ai/conversations/new", {"product_ids": []})[
        "id"
    ]
    service.action(
        ai_conn,
        f"/api/ai/conversations/{cid}/messages",
        {"message": "Remember bikepacking"},
    )
    path = ai_conn.execute("PRAGMA database_list").fetchone()["file"]
    other = db.init_db(path)
    try:
        assert (
            service.snapshot(other)["conversations"][0]["messages"][0]["content"]
            == "Remember bikepacking"
        )
    finally:
        other.close()


def test_source_assessments_reject_invented_ids_and_preserve_taxonomy():
    from subitoo.ai.schemas import validate_research

    research = {
        "criteria": {},
        "strengths": [],
        "weaknesses": [],
        "known_issues": [],
        "specifications": {},
        "summary": "cached",
        "confidence": 0.5,
        "source_assessments": {
            "s": {
                "source_type": "professional_review",
                "reliability": "moderate",
                "rationale": "Technical test, single publisher",
            }
        },
    }
    assert (
        validate_research(research, {"s"})["source_assessments"]["s"]["source_type"]
        == "professional_review"
    )
    with pytest.raises(ValueError):
        validate_research(research, {"other"})


def test_chat_citations_survive_category_filter(ai_conn):
    enable(ai_conn)
    pid = store.save_identity(ai_conn, 1, identity())
    evidence = [
        {
            "id": "s",
            "url": "https://example.org/review",
            "title": "Review",
            "text": "Evidence",
            "retrieved_at": store.now(),
        }
    ]
    research = {
        "criteria": {},
        "strengths": [],
        "weaknesses": [],
        "known_issues": [],
        "specifications": {},
        "summary": "cached",
        "confidence": 0.5,
    }
    store.save_research(ai_conn, pid, research, evidence)
    cid = service.action(ai_conn, "/api/ai/conversations/new", {"product_ids": [pid]})[
        "id"
    ]
    ai_conn.execute(
        "INSERT INTO ai_messages(conversation_id,role,message,source_ids_json,created_at) VALUES(?,'assistant','Evidence',?,0)",
        (cid, '["s"]'),
    )
    msg = service.snapshot(ai_conn, category="camera")["conversations"][0]["messages"][
        0
    ]
    assert msg["sources"][0]["url"] == "https://example.org/review"


def test_exact_unique_model_reuses_research_without_separate_generation(ai_conn):
    info = identity()
    info["generation"] = None
    first = store.save_identity(ai_conn, 1, info)
    ai_conn.execute(
        "INSERT INTO listings(query_id,site_listing_id,title,first_seen_at,last_seen_at) VALUES(1,'other','Acme Box 2',1,1)"
    )
    assert store.save_identity(ai_conn, 2, info) == first


def test_external_links_in_chat_not_accepted_as_unverified_citations():
    from subitoo.ai.schemas import validate_chat

    with pytest.raises(ValueError):
        validate_chat(
            {"message": "See https://fabricated.example/review", "source_ids": []},
            set(),
        )


def test_chat_job_not_starved_by_existing_identification_backlog(ai_conn):
    enable(ai_conn)
    store.discover(ai_conn)
    cid = service.action(ai_conn, "/api/ai/conversations/new", {"product_ids": []})[
        "id"
    ]
    service.action(
        ai_conn,
        f"/api/ai/conversations/{cid}/messages",
        {"message": "Are any products analyzed?"},
    )
    worker = Worker(ai_conn, llm=FakeLLM(), search=FakeSearch())
    worker.run_once()
    assert (
        service.snapshot(ai_conn)["conversations"][0]["messages"][-1]["role"]
        == "assistant"
    )


def test_comparison_requires_two_products_in_backend(ai_conn):
    enable(ai_conn)
    pid = store.save_identity(ai_conn, 1, identity())
    with pytest.raises(ValueError):
        service.action(ai_conn, "/api/ai/compare", {"product_ids": [pid]})


def test_identity_category_normalization_and_empty_identifiers():
    from subitoo.ai.schemas import validate_identity

    info = identity()
    info["category"] = " Camera "
    info["variant"] = "   "
    normalized = validate_identity(info)
    assert normalized["category"] == "camera" and normalized["variant"] is None


def test_tracking_parameters_do_not_defeat_canonical_dedup():
    assert (
        store.canonical_url("https://EXAMPLE.org/item?id=4&utm_source=mail#top")
        == "https://example.org/item?id=4"
    )


def test_explicit_natural_language_search_request_queues_research(ai_conn):
    enable(ai_conn)
    cid = service.action(ai_conn, "/api/ai/conversations/new", {"product_ids": []})[
        "id"
    ]
    service.action(
        ai_conn,
        f"/api/ai/conversations/{cid}/messages",
        {"message": "Cerca su Reddit problemi ricorrenti", "research": False},
    )
    payload = json.loads(
        ai_conn.execute(
            "SELECT payload_json FROM ai_jobs WHERE kind='chat'"
        ).fetchone()[0]
    )
    assert payload["research"] is True
