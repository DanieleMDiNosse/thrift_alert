"""Deterministic on-demand analysis: SQLite + fake inference/retrieval, no network."""

import json
import pytest
from subitoo.core import db
from subitoo.ai.config import AIConfig


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(str(tmp_path / "worth.db"))
    c.execute(
        "INSERT INTO queries(name,site,search_json,filters_json,cron,status_changed_at,created_at,updated_at) VALUES ('test','subito','{}','{}','* * * * *',0,0,0)"
    )
    c.execute(
        "INSERT INTO listings(query_id,site_listing_id,title,last_price,currency,url,raw_json,first_seen_at,last_seen_at) VALUES (1,'abc','Acme Box 2',40,'EUR','https://example.com/ad','{\"body\":\"Box 2 standard, graffi\"}',1,1)"
    )
    yield c
    c.close()


@pytest.fixture
def cfg():
    return AIConfig(_env_file=None, enabled=False)


class Search:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def search(self, query):
        self.calls.append(query)
        if self.fail:
            raise ValueError("offline")
        return [
            {
                "url": "https://example.com/shop",
                "title": "Shop",
                "snippet": "invented 999 EUR",
            }
        ]

    def fetch_page(self, url):
        return dict(
            url=url,
            title="Shop",
            text="Acme Box 2 standard nuovo disponibile: EUR 129,90.",
            retrieved_at=123,
        )


class LLM:
    def __init__(self, invalid=None):
        self.calls = []
        self.invalid = invalid

    def complete(self, task, context):
        self.calls.append((task, context))
        if task == "identity":
            return dict(
                category="audio",
                manufacturer="Acme",
                name="Box",
                model="Box",
                generation="2",
                variant="standard",
                confidence=0.9,
            )
        sources = context["sources"]
        if not sources:
            return dict(message="Controllare i graffi.", source_ids=[], new_price=None)
        sid = sources[0]["id"]
        value = dict(
            message="Verificare graffi, accessori e spese prima di acquistare.",
            source_ids=[sid],
            new_price=dict(
                amount=129.9,
                currency="EUR",
                source_id=sid,
                quote="Acme Box 2 standard nuovo disponibile: EUR 129,90.",
                kind="current_new",
                availability="in_stock",
            ),
        )
        if self.invalid == "quote":
            value["new_price"]["quote"] = "EUR 129,90 prezzo inventato"
        if self.invalid == "amount":
            value["new_price"]["amount"] = 999
        if self.invalid == "citation":
            value["source_ids"] = ["invented"]
        return value


def queued(c, cfg):
    from subitoo.ai import worth

    response = worth.request(c, "subito", "abc", config=cfg)
    job = c.execute(
        "SELECT * FROM ai_jobs WHERE id=?", (response["job_id"],)
    ).fetchone()
    return dict(job), json.loads(job["payload_json"])


def finish(c, cfg, llm=None, search=None):
    from subitoo.ai import worth

    job, payload = queued(c, cfg)
    bundle = worth.analyze(
        c, payload, llm or LLM(), search=search or Search(), config=cfg
    )
    worth.save(c, job["target_id"], payload, bundle)
    c.execute("UPDATE ai_jobs SET status='done' WHERE id=?", (job["id"],))
    return worth.read(c, "subito", "abc", config=cfg)


def test_public_contract_and_paused_queue_dedup(conn, cfg):
    from subitoo.ai import worth

    initial = worth.read(conn, "subito", "abc", config=cfg)
    assert set(initial) == {
        "status",
        "stale",
        "result",
        "sources",
        "listing",
        "analyzed_at",
        "error",
        "job_id",
    }
    assert initial["status"] == "not_analyzed"
    before = dict(conn.execute("SELECT * FROM listings").fetchone())
    job, payload = queued(conn, cfg)
    again = worth.request(conn, "subito", "abc", refresh=True, config=cfg)
    assert again["job_id"] == job["id"] and again["status"] == "queued"
    assert len(job["target_id"]) == 64 and job["kind"] == "worth"
    assert payload["listing"]["description"] == "Box 2 standard, graffi"
    assert conn.execute("SELECT enabled FROM ai_settings").fetchone()[0] is None
    assert dict(conn.execute("SELECT * FROM listings").fetchone()) == before


def test_pipeline_cache_restart_and_stale(conn, cfg):
    from subitoo.ai import worth

    llm, search = LLM(), Search()
    response = finish(conn, cfg, llm, search)
    assert response["status"] == "completed" and not response["stale"]
    assert response["result"]["new_price"]["amount"] == 129.9
    assert len(search.calls) == 2 and "prezzo nuovo" in search.calls[1]
    assert [t for t, _ in llm.calls] == ["identity", "worth"]
    assert worth.request(conn, "subito", "abc", config=cfg)["status"] == "completed"
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    other = db.init_db(path)
    assert (
        worth.read(other, "subito", "abc", config=cfg)["result"] == response["result"]
    )
    other.close()
    conn.execute("UPDATE listings SET last_price=30")
    assert worth.read(conn, "subito", "abc", config=cfg)["stale"]
    assert worth.request(conn, "subito", "abc", config=cfg)["status"] == "queued"


@pytest.mark.parametrize("invalid", ["quote", "amount", "citation"])
def test_unverifiable_model_output_rejected(conn, cfg, invalid):
    from subitoo.ai import worth

    _, payload = queued(conn, cfg)
    with pytest.raises(ValueError):
        worth.analyze(conn, payload, LLM(invalid), search=Search(), config=cfg)


def test_search_failure_is_honest_success(conn, cfg):
    result = finish(conn, cfg, search=Search(fail=True))
    assert result["status"] == "completed" and result["sources"] == []
    assert result["result"]["new_price"] is None
    assert "prezzo del nuovo" in result["result"]["message"].lower()


def test_failed_refresh_retains_completed_and_expiry(conn, cfg):
    from subitoo.ai import worth

    previous = finish(conn, cfg)
    conn.execute("UPDATE ai_listing_worth SET analyzed_at=1")
    assert worth.read(conn, "subito", "abc", config=cfg)["stale"]
    response = worth.request(conn, "subito", "abc", config=cfg)
    conn.execute(
        "UPDATE ai_jobs SET status='failed',error='Operazione fallita' WHERE id=?",
        (response["job_id"],),
    )
    failed = worth.read(conn, "subito", "abc", config=cfg)
    assert failed["status"] == "failed" and failed["error"]
    assert failed["result"] == previous["result"]


def test_inflight_snapshot_is_immutable_and_old_save_cannot_overwrite(conn, cfg):
    from subitoo.ai import worth

    job, payload = queued(conn, cfg)
    conn.execute("UPDATE listings SET title='Other model'")
    result = worth.analyze(conn, payload, LLM(), search=Search(), config=cfg)
    assert result["listing"]["title"] == "Acme Box 2"
    conn.execute("UPDATE ai_jobs SET status='failed' WHERE id=?", (job["id"],))
    worth.request(conn, "subito", "abc", config=cfg)
    assert worth.save(conn, job["target_id"], payload, result) is False
    assert worth.read(conn, "subito", "abc", config=cfg)["result"] is None


@pytest.mark.parametrize(
    "value",
    [
        dict(message=" ", source_ids=[]),
        dict(message="https://invented.test", source_ids=[]),
        dict(message="ok", source_ids=["bad"]),
        dict(message="ok", source_ids=[], score=90),
    ],
)
def test_strict_schema(value):
    from subitoo.ai.worth import validate_worth

    with pytest.raises(ValueError):
        validate_worth(value, set())


@pytest.mark.parametrize(
    "site,native,refresh",
    [("bad", "abc", False), ("subito", "", False), ("subito", "abc", "false")],
)
def test_request_validation(conn, cfg, site, native, refresh):
    from subitoo.ai import worth

    with pytest.raises(ValueError):
        worth.request(conn, site, native, refresh=refresh, config=cfg)


def test_capacity_atomic_and_missing_listing(conn, cfg):
    from subitoo.ai import worth
    from subitoo.ai import store

    bounded = cfg.model_copy(update={"max_pending_jobs": 1})
    store.enqueue(conn, "chat", "elsewhere", config=bounded)
    with pytest.raises(ValueError):
        worth.request(conn, "subito", "abc", config=bounded)
    assert conn.execute("SELECT count(*) FROM ai_listing_worth").fetchone()[0] == 0
    with pytest.raises(ValueError):
        worth.request(conn, "subito", "missing", config=cfg)


def test_context_bounded_and_snippets_not_evidence(conn, cfg):
    from subitoo.ai import worth

    conn.execute(
        "UPDATE listings SET raw_json=?",
        (json.dumps({"body": "advertisement " * 10000}),),
    )
    job, payload = queued(conn, cfg)
    llm = LLM()
    bounded = cfg.model_copy(update={"max_context_chars": 2500})
    worth.analyze(conn, payload, llm, search=Search(), config=bounded)
    for _, context in llm.calls:
        assert len(json.dumps(context, ensure_ascii=False, sort_keys=True)) <= 2500
        assert "invented 999" not in json.dumps(context)


def test_newest_original_facts_and_cached_description(conn, cfg):
    from subitoo.ai import worth

    conn.execute(
        "INSERT INTO queries(name,site,search_json,filters_json,cron,status_changed_at,created_at,updated_at) VALUES ('second','subito','{}','{}','* * * * *',0,0,0)"
    )
    conn.execute(
        "INSERT INTO listings(query_id,site_listing_id,title,last_price,currency,raw_json,first_seen_at,last_seen_at) VALUES(2,'abc','Latest Box',50,'EUR','{}',2,2)"
    )
    conn.execute(
        "INSERT INTO ai_marketplace_facts(site,site_listing_id,data_json,updated_at) VALUES('subito','abc','{\"condition\":\"good\",\"fees\":3}',1)"
    )
    listing = worth.read(conn, "subito", "abc", config=cfg)["listing"]
    assert listing["title"] == "Latest Box" and listing["price"] == 50
    assert (
        listing["facts"]["fees"] == 3
        and listing["facts"]["facts_sources"]["condition"] == "user"
    )
    _, payload = queued(conn, cfg)
    conn.execute("UPDATE ai_marketplace_facts SET data_json='{\"fees\":4}'")
    assert worth.read(conn, "subito", "abc", config=cfg)["stale"]
    assert payload["listing"]["facts"]["fees"] == 3


def test_same_fingerprint_refresh_token_blocks_old_result(conn, cfg):
    from subitoo.ai import worth

    job, payload = queued(conn, cfg)
    result = worth.analyze(conn, payload, LLM(), search=Search(), config=cfg)
    conn.execute("UPDATE ai_jobs SET status='failed' WHERE id=?", (job["id"],))
    worth.request(conn, "subito", "abc", refresh=True, config=cfg)
    assert worth.save(conn, job["target_id"], payload, result) is False


@pytest.mark.parametrize(
    "amount,currency",
    [
        (float("inf"), "EUR"),
        (float("nan"), "EUR"),
        (-1, "EUR"),
        (True, "EUR"),
        (10, "eur"),
    ],
)
def test_price_schema_rejects_invalid_values(amount, currency):
    from subitoo.ai.worth import validate_worth

    with pytest.raises(ValueError):
        validate_worth(
            dict(
                message="ok",
                source_ids=["s"],
                new_price=dict(
                    amount=amount,
                    currency=currency,
                    source_id="s",
                    quote="EUR 10",
                    kind="current_new",
                    availability="in_stock",
                ),
            ),
            {"s"},
        )


def test_empty_retrieval_never_cites_search_snippets(conn, cfg):
    from subitoo.ai import worth

    class EmptySearch(Search):
        def fetch_page(self, url):
            return dict(text="  ", title="Empty")

    _, payload = queued(conn, cfg)
    result = worth.analyze(conn, payload, LLM(), search=EmptySearch(), config=cfg)
    assert result["sources"] == [] and result["result"]["new_price"] is None


def test_worker_processes_explicit_request_while_legacy_paused(conn, cfg):
    from subitoo.ai import worth, store
    from subitoo.ai.worker import Worker

    store.enqueue(conn, "identify", "1", config=cfg)
    worth.request(conn, "subito", "abc", config=cfg)
    worker = Worker(conn, config=cfg, llm=LLM(), search=Search())
    assert worker.run_once()
    assert worth.read(conn, "subito", "abc", config=cfg)["status"] == "completed"
    assert (
        conn.execute("SELECT status FROM ai_jobs WHERE kind='identify'").fetchone()[0]
        == "queued"
    )
    assert not worker.run_once()


def test_read_is_side_effect_free_and_cached_description_is_original(conn, cfg):
    from subitoo.ai import worth

    conn.execute(
        "INSERT INTO listing_descriptions(site,site_listing_id,description,fetched_at) VALUES('subito','abc','Original full text',10)"
    )
    before = conn.total_changes
    listing = worth.read(conn, "subito", "abc", config=cfg)["listing"]
    assert listing["description"] == "Original full text"
    assert conn.total_changes == before
    _, payload = queued(conn, cfg)
    assert payload["listing"]["description"] == "Original full text"


def test_missing_credentials_does_not_create_job(conn, cfg):
    from subitoo.ai import worth

    config = cfg.model_copy(update={"provider": "openai"})
    with pytest.raises(ValueError, match="AI_API_KEY"):
        worth.request(conn, "subito", "abc", config=config)
    assert conn.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0
