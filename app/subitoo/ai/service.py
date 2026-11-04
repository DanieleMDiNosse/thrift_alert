"""Synchronous UI operations only enqueue work; worker owns network/inference."""

import math
import re
import uuid
from . import store
from .ranking import rank


def grouped_rankings(products, listings, weights=None, hypothetical=None):
    grouped = {}
    for category in sorted({p["category"] for p in products}):
        selected = [p for p in products if p["category"] == category]
        ids = {p["id"] for p in selected}
        grouped[category] = rank(
            selected,
            [listing for listing in listings if listing["product_id"] in ids],
            weights,
            hypothetical,
        )
    if len(grouped) == 1:
        return {**next(iter(grouped.values())), "by_category": grouped}
    # Cross-category ordinal rankings would be misleading; retain evaluations by category.
    return {
        "quality": [],
        "value": [],
        "asking_value": [],
        "deal": [],
        "listings": [listing for g in grouped.values() for listing in g["listings"]],
        "by_category": grouped,
    }


def snapshot(conn, category=None, watch_id=None):
    with store.atomic(conn):
        settings = store.settings(conn)
        products = store.products(conn)
        listings = store.listings(conn, watch_id)
        if watch_id is not None:
            ids = {listing["product_id"] for listing in listings}
            products = [p for p in products if p["id"] in ids]
        if category:
            products = [p for p in products if p["category"] == category]
            ids = {p["id"] for p in products}
            listings = [listing for listing in listings if listing["product_id"] in ids]
        return {
            "products": products,
            "listings": listings,
            "rankings": grouped_rankings(products, listings, settings["weights"]),
            "conversations": store.conversations(conn),
            "jobs": store.jobs(conn),
            "job_summary": store.job_summary(conn),
            "settings": settings,
        }


def _product_ids(conn, value):
    if (
        not isinstance(value, list)
        or len(value) > 20
        or any(not isinstance(x, str) or not x or len(x) > 100 for x in value)
    ):
        raise ValueError("Provide at most 20 product IDs")
    ids = list(dict.fromkeys(value))
    known = {p["id"] for p in store.products(conn)}
    if not set(ids) <= known:
        raise ValueError("Product not found")
    return ids


def _watch(conn, value):
    if value is not None and (
        type(value) is not int
        or not conn.execute("SELECT id FROM watches WHERE id=?", (value,)).fetchone()
    ):
        raise ValueError("Watch not found")
    return value


def _hypothetical(value):
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError("Invalid hypothetical prices")
    for key, price in value.items():
        if (
            not isinstance(key, str)
            or len(key) > 100
            or type(price) not in (int, float)
            or not math.isfinite(price)
            or price <= 0
        ):
            raise ValueError("Hypothetical prices must be finite positive numbers")
    return value


def action(conn, path, data):
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    if path == "/api/ai/settings":
        if set(data) - {"enabled", "weights"}:
            raise ValueError("Unknown setting")
        if "enabled" in data and type(data["enabled"]) is not bool:
            raise ValueError("Enabled must be boolean")
        cfg = store.get_config()
        if data.get("enabled") and cfg.configuration_error():
            raise ValueError(cfg.configuration_error())
        weights = data.get("weights", store.settings(conn)["weights"])
        if not isinstance(weights, dict):
            raise ValueError("Invalid ranking weights")
        try:
            rank([], [], weights)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Invalid ranking weights") from None
        with store.atomic(conn):
            conn.execute(
                "UPDATE ai_settings SET enabled=?,weights_json=? WHERE id=1",
                (
                    int(data.get("enabled", store.settings(conn)["enabled"])),
                    store.dumps(weights),
                ),
            )
        return store.settings(conn)
    match = re.fullmatch(r"/api/ai/jobs/([1-9][0-9]*)/retry", path)
    if match:
        with store.atomic(conn):
            row = conn.execute(
                "SELECT * FROM ai_jobs WHERE id=?", (int(match[1]),)
            ).fetchone()
            if not row:
                raise ValueError("Attività non trovata")
            if row["status"] in ("queued", "running", "done"):
                return {"id": row["id"], "status": row["status"]}
            other = conn.execute(
                "SELECT 1 FROM ai_jobs WHERE kind=? AND target_id=? AND status IN ('queued','running')",
                (row["kind"], row["target_id"]),
            ).fetchone()
            if other:
                raise ValueError("Esiste già un tentativo in corso")
            active = conn.execute(
                "SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if active >= store.get_config().max_pending_jobs:
                raise ValueError(
                    "Coda piena; attendi il completamento di alcune attività"
                )
            conn.execute(
                "UPDATE ai_jobs SET status='queued',attempts=0,error=NULL,error_code=NULL,available_at=?,updated_at=?,priority=10 WHERE id=?",
                (store.now(), store.now(), row["id"]),
            )
        return {"id": row["id"], "status": "queued"}
    if not store.settings(conn)["enabled"]:
        raise ValueError("AI is disabled")
    match = re.fullmatch(r"/api/ai/listings/([1-9][0-9]*)/facts", path)
    if match:
        from .schemas import ListingFacts

        lid = int(match[1])
        original = conn.execute(
            "SELECT q.site,l.site_listing_id FROM listings l JOIN queries q ON q.id=l.query_id WHERE l.id=?",
            (lid,),
        ).fetchone()
        if not original:
            raise ValueError("Listing not found")
        facts = ListingFacts.model_validate(data).model_dump()
        conn.execute(
            "INSERT INTO ai_marketplace_facts(site,site_listing_id,data_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(site,site_listing_id) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at",
            (
                original["site"],
                original["site_listing_id"],
                store.dumps(facts),
                store.now(),
            ),
        )
        return {"id": lid, "facts": facts}
    if path == "/api/ai/sync":
        wid = _watch(conn, data.get("watch_id"))
        limit = data.get("limit", 5)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Seleziona da 1 a 20 annunci per volta")
        with store.atomic(conn):
            count = store.discover(conn, limit=limit, watch_id=wid)
            rows = conn.execute(
                """SELECT j.id FROM ai_jobs j JOIN listings l ON j.target_id=CAST(l.id AS TEXT)
                WHERE j.kind='identify' AND j.status='queued' AND (? IS NULL OR EXISTS(SELECT 1 FROM watch_sources w WHERE w.query_id=l.query_id AND w.watch_id=?))
                ORDER BY j.priority DESC,j.id LIMIT ?""",
                (wid, wid, limit),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE ai_jobs SET priority=10,available_at=? WHERE id=?",
                    (store.now(), row["id"]),
                )
        return {"enqueued": count, "prioritized": len(rows), "limit": limit}
    match = re.fullmatch(r"/api/ai/products/([^/]+)/refresh", path)
    if match:
        pid = _product_ids(conn, [match[1]])[0]
        with store.atomic(conn):
            jid = store.enqueue(conn, "research", pid)
            if jid:
                conn.execute(
                    "UPDATE ai_products SET research_status='pending',updated_at=? WHERE id=?",
                    (store.now(), pid),
                )
        return {"status": "queued", "job_id": jid, "product_id": pid}
    if path == "/api/ai/compare":
        ids = _product_ids(conn, data.get("product_ids", []))
        if len(ids) < 2:
            raise ValueError("Select at least two products to compare")
        products = [p for p in store.products(conn) if p["id"] in ids]
        listings = [
            listing for listing in store.listings(conn) if listing["product_id"] in ids
        ]
        return {
            "products": products,
            "listings": listings,
            "rankings": grouped_rankings(
                products,
                listings,
                store.settings(conn)["weights"],
                _hypothetical(data.get("hypothetical", {})),
            ),
        }
    if path == "/api/ai/conversations/new":
        ids = _product_ids(conn, data.get("product_ids", []))
        wid = _watch(conn, data.get("watch_id"))
        cid = uuid.uuid4().hex
        ts = store.now()
        conn.execute(
            "INSERT INTO ai_conversations(id,product_ids_json,watch_id,created_at,updated_at) VALUES(?,?,?,?,?)",
            (cid, store.dumps(ids), wid, ts, ts),
        )
        return {"id": cid, "product_ids": ids, "watch_id": wid, "messages": []}
    match = re.fullmatch(r"/api/ai/conversations/([^/]+)/(messages|clear)", path)
    if not match:
        raise ValueError("Unknown AI action")
    cid, operation = match.groups()
    conversation = conn.execute(
        "SELECT * FROM ai_conversations WHERE id=?", (cid,)
    ).fetchone()
    if not conversation:
        raise ValueError("Conversation not found")
    if operation == "clear":
        with store.atomic(conn):
            conn.execute("DELETE FROM ai_messages WHERE conversation_id=?", (cid,))
            # Revision prevents a running reply from resurrecting cleared history.
            conn.execute(
                "UPDATE ai_conversations SET revision=revision+1,updated_at=? WHERE id=?",
                (store.now(), cid),
            )
            conn.execute(
                "UPDATE ai_jobs SET status='cancelled',updated_at=? WHERE kind='chat' AND target_id=? AND status IN ('queued','running')",
                (store.now(), cid),
            )
        return {"id": cid, "messages": []}
    text = data.get("message")
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        raise ValueError("Message must be 1–8000 characters")
    research = data.get("research", False)
    if type(research) is not bool:
        raise ValueError("Research must be boolean")
    # Only an explicit imperative triggers fresh network research automatically.
    # Ordinary follow-up questions continue to use cached evidence.
    research = research or bool(
        re.match(
            r"^\s*(?:(?:please|per favore)[,\s]+)?(?:search|find|look up|cerca|cercami|trova|ricerca)\b",
            text,
            re.IGNORECASE,
        )
    )
    hypothetical = _hypothetical(data.get("hypothetical", {}))
    with store.atomic(conn):
        jid = store.enqueue(
            conn,
            "chat",
            cid,
            {
                "research": research,
                "hypothetical": hypothetical,
                "revision": conversation["revision"],
            },
        )
        if not jid:
            raise ValueError("Conversation reply is pending")
        conn.execute(
            "INSERT INTO ai_messages(conversation_id,role,message,created_at) VALUES(?,'user',?,?)",
            (cid, text.strip(), store.now()),
        )
        conn.execute(
            "UPDATE ai_conversations SET updated_at=? WHERE id=?", (store.now(), cid)
        )
    return {"status": "queued", "job_id": jid, "conversation_id": cid}


def get(conn, path):
    match = re.fullmatch(r"/api/ai/conversations/([^/]+)", path)
    if match:
        for item in store.conversations(conn):
            if item["id"] == match[1]:
                return item
        raise ValueError("Conversation not found")
    if path in {"/api/ai", "/api/ai/snapshot"}:
        return snapshot(conn)
    raise ValueError("Unknown AI resource")
