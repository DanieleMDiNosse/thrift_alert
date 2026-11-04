"""SQLite-only AI state; no network or inference inside these operations."""

from contextlib import contextmanager
import hashlib
import json
import time
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .config import get_config
from .schemas import validate_identity, validate_research, canonical_category
from .marketplace_facts import marketplace_facts


def now():
    return int(time.time())


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


@contextmanager
def atomic(conn):
    name = "ai_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + name)
    try:
        yield
        conn.execute("RELEASE " + name)
    except BaseException:
        conn.execute("ROLLBACK TO " + name)
        conn.execute("RELEASE " + name)
        raise


def settings(conn, config=None):
    cfg = config or get_config()
    row = conn.execute(
        "SELECT enabled,weights_json FROM ai_settings WHERE id=1"
    ).fetchone()
    return {
        **cfg.public(),
        "enabled": cfg.enabled if row["enabled"] is None else bool(row["enabled"]),
        "weights": json.loads(row["weights_json"]),
    }


def enqueue(conn, kind, target_id, payload=None, config=None):
    cfg = config or get_config()
    ts = now()
    existing = conn.execute(
        "SELECT id FROM ai_jobs WHERE kind=? AND target_id=? AND status IN ('queued','running')",
        (kind, str(target_id)),
    ).fetchone()
    if existing:
        return None
    if (
        conn.execute(
            "SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')"
        ).fetchone()[0]
        >= cfg.max_pending_jobs
    ):
        raise ValueError("AI queue is full")
    cur = conn.execute(
        "INSERT OR IGNORE INTO ai_jobs(kind,target_id,payload_json,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (kind, str(target_id), dumps(payload or {}), ts, ts, ts),
    )
    return cur.lastrowid if cur.rowcount else None


def discover(conn, config=None, limit=100, watch_id=None):
    rows = conn.execute(
        """SELECT l.id FROM listings l
        LEFT JOIN ai_listing_products a ON a.listing_id=l.id
        WHERE a.listing_id IS NULL AND NOT EXISTS
        (SELECT 1 FROM ai_jobs j WHERE j.kind='identify' AND j.target_id=CAST(l.id AS TEXT) AND j.status!='cancelled')
        AND (? IS NULL OR EXISTS(SELECT 1 FROM watch_sources w WHERE w.query_id=l.query_id AND w.watch_id=?))
        ORDER BY l.id LIMIT ?""",
        (watch_id, watch_id, limit),
    ).fetchall()
    count = 0
    with atomic(conn):
        capacity = (config or get_config()).max_pending_jobs - conn.execute(
            "SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')"
        ).fetchone()[0]
        identification_capacity = (
            config or get_config()
        ).identification_backlog - conn.execute(
            "SELECT count(*) FROM ai_jobs WHERE kind='identify' AND status IN ('queued','running')"
        ).fetchone()[0]
        for row in rows:
            if capacity <= 0 or identification_capacity <= 0:
                break
            if enqueue(conn, "identify", row["id"], config=config):
                count += 1
                capacity -= 1
                identification_capacity -= 1
        cutoff = now() - (config or get_config()).research_ttl_days * 86400
        for product in conn.execute(
            "SELECT id FROM ai_products WHERE research_status='ready' AND researched_at<?",
            (cutoff,),
        ):
            if capacity <= 0:
                break
            if enqueue(conn, "research", product["id"], config=config):
                count += 1
                capacity -= 1
    return count


def canonical_url(url):
    try:
        parsed = urlsplit(url or "")
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.casefold().startswith("utm_")
                and key.casefold() not in {"fbclid", "gclid"}
            ]
        )
        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path,
                query,
                "",
            )
        )
    except ValueError:
        return None


def save_identity(conn, listing_id, identity):
    identity = validate_identity(identity)
    original = conn.execute(
        "SELECT l.*,q.site FROM listings l JOIN queries q ON q.id=l.query_id WHERE l.id=?",
        (listing_id,),
    ).fetchone()
    if not original:
        raise ValueError("Listing not found")
    fields = ["category", "manufacturer", "model", "generation", "variant"]
    # A partially identified product is isolated by marketplace listing identity.
    known = (
        all(identity.get(k) for k in ["category", "manufacturer", "model", "variant"])
        and identity["confidence"] >= 0.8
    )
    key = (
        dumps([(identity.get(k) or "").strip().casefold() for k in fields])
        if known
        else dumps(["unresolved", original["site"], original["site_listing_id"]])
    )
    pid = hashlib.sha256(key.encode()).hexdigest()[:32]
    ts = now()
    with atomic(conn):
        conn.execute(
            "INSERT OR IGNORE INTO ai_products(id,identity_key,category,data_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (pid, key, identity["category"], dumps(identity), ts, ts),
        )
        # Duplicate search hits retain original rows but share exact site/native identity.
        conn.execute(
            "INSERT INTO ai_listing_products(listing_id,product_id,confidence) VALUES(?,?,?) ON CONFLICT(listing_id) DO UPDATE SET product_id=excluded.product_id,confidence=excluded.confidence",
            (listing_id, pid, identity["confidence"]),
        )
    return pid


def sources(conn, pid):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM ai_sources WHERE product_id=? ORDER BY retrieved_at,id",
            (pid,),
        )
    ]


def products(conn):
    cutoff = now() - get_config().research_ttl_days * 86400
    result = []
    active = {
        r["target_id"]: r["status"]
        for r in conn.execute(
            "SELECT target_id,status FROM ai_jobs WHERE kind='research' AND status IN ('queued','running')"
        )
    }
    for r in conn.execute("SELECT * FROM ai_products ORDER BY created_at,id"):
        status = r["research_status"]
        if status == "ready" and (r["researched_at"] or 0) < cutoff:
            status = "needs_refresh"
        if r["id"] in active:
            status = "researching" if active[r["id"]] == "running" else "queued"
        result.append(
            {
                **json.loads(r["data_json"]),
                "category": canonical_category(r["category"]),
                "original_category": r["category"],
                "id": r["id"],
                "research_status": status,
                "researched_at": r["researched_at"],
                "research": json.loads(r["research_json"]),
                "sources": sources(conn, r["id"]),
            }
        )
    return result


def save_research(conn, pid, research, evidence, *, supplement=False):
    allowed = {s["id"] for s in evidence}
    researched_at = now()
    if supplement:
        previous = conn.execute(
            "SELECT research_json,researched_at FROM ai_products WHERE id=?", (pid,)
        ).fetchone()
        old = json.loads(previous["research_json"]) if previous else {}
        allowed.update(s["id"] for s in sources(conn, pid))
        if old:
            research = {**research}
            for field in ["criteria", "specifications", "source_assessments"]:
                research[field] = {**old.get(field, {}), **research.get(field, {})}
            for field in ["strengths", "weaknesses", "known_issues"]:
                research[field] = list(
                    dict.fromkeys(old.get(field, []) + research.get(field, []))
                )[:20]
            research["summary"] = (
                old.get("summary", "")
                + "\nAdditional focused evidence: "
                + research["summary"]
            )[:6000]
            research["confidence"] = min(
                old.get("confidence", 0), research["confidence"]
            )
            # A targeted reliability lookup must not reset the broad research TTL.
            researched_at = previous["researched_at"] or researched_at
    research = validate_research(research, allowed)
    with atomic(conn):
        for source in evidence:
            conn.execute(
                "INSERT OR IGNORE INTO ai_sources(id,product_id,url,title,text,retrieved_at,publication_date,kind,reliability) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    source["id"],
                    pid,
                    source["url"],
                    source["title"],
                    source["text"],
                    source["retrieved_at"],
                    source.get("publication_date"),
                    source.get("kind", "web"),
                    source.get("reliability", "unassessed"),
                ),
            )
        conn.execute(
            "UPDATE ai_products SET research_json=?,research_status='ready',updated_at=?,researched_at=? WHERE id=?",
            (dumps(research), now(), researched_at, pid),
        )


def listings(conn, watch_id=None):
    sql = """SELECT l.*,q.site,a.product_id,a.confidence FROM listings l JOIN queries q ON q.id=l.query_id LEFT JOIN ai_listing_products a ON a.listing_id=l.id"""
    params = ()
    if watch_id is not None:
        sql += " WHERE EXISTS(SELECT 1 FROM watch_sources w WHERE w.query_id=l.query_id AND w.watch_id=?)"
        params = (watch_id,)
    rows = conn.execute(sql + " ORDER BY l.last_seen_at DESC,l.id", params).fetchall()
    # The newest duplicate may not have been identified yet. Exact marketplace
    # identity can safely reuse a known association from another search observation.
    associations = {}
    for row in conn.execute(
        "SELECT q.site,l.site_listing_id,a.product_id,a.confidence FROM ai_listing_products a JOIN listings l ON l.id=a.listing_id JOIN queries q ON q.id=l.query_id ORDER BY l.last_seen_at,l.id"
    ):
        associations[(row["site"], row["site_listing_id"])] = (
            row["product_id"],
            row["confidence"],
        )
    result = []
    seen = set()
    for r in rows:
        native = (r["site"], r["site_listing_id"])
        url = canonical_url(r["url"])
        if native in seen or (url and ("url", url) in seen):
            continue
        seen.add(native)
        if url:
            seen.add(("url", url))
        result.append(
            {
                "id": r["id"],
                "product_id": r["product_id"] or associations.get(native, (None, 0))[0],
                "site": r["site"],
                "site_listing_id": r["site_listing_id"],
                "title": r["title"],
                "price": r["last_price"],
                "currency": r["currency"],
                "url": r["url"],
                "image_url": r["image_url"],
                "last_seen_at": r["last_seen_at"],
                **marketplace_facts(r["site"], r["raw_json"], r["currency"]),
                "variant": None,
                "confidence": r["confidence"] or associations.get(native, (None, 0))[1],
            }
        )
    variants = {p["id"]: p.get("variant") for p in products(conn)}
    for item in result:
        item["variant"] = variants.get(item["product_id"])
    facts = {
        (r["site"], r["site_listing_id"]): json.loads(r["data_json"])
        for r in conn.execute("SELECT * FROM ai_marketplace_facts")
    }
    for item in result:
        native = (item["site"], item["site_listing_id"])
        if native in facts:
            item.update(facts[native])
            item["facts_sources"].update({key: "user" for key in facts[native]})
            if "condition" in facts[native]:
                item["condition_label"] = None
            item["facts_provenance"] = "user supplied; verify with seller"
    return result


def messages(conn, cid):
    result = []
    for r in conn.execute(
        "SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id", (cid,)
    ):
        ids = json.loads(r["source_ids_json"])
        evidence = []
        if ids:
            marks = ",".join("?" for _ in ids)
            evidence = [
                dict(s)
                for s in conn.execute(
                    f"SELECT id,title,url,kind,reliability,retrieved_at FROM ai_sources WHERE id IN ({marks})",
                    ids,
                )
            ]
        result.append(
            {
                "id": r["id"],
                "role": r["role"],
                "message": r["message"],
                "content": r["message"],
                "source_ids": ids,
                "sources": evidence,
                "created_at": r["created_at"],
            }
        )
    return result


def conversations(conn):
    return [
        {
            "id": r["id"],
            "product_ids": json.loads(r["product_ids_json"]),
            "watch_id": r["watch_id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "messages": messages(conn, r["id"]),
        }
        for r in conn.execute(
            "SELECT * FROM ai_conversations ORDER BY updated_at DESC,id"
        )
    ]


def jobs(conn):
    return [
        dict(r)
        for r in conn.execute(
            """SELECT j.id,j.kind,j.target_id,j.status,j.attempts,j.available_at,j.created_at,j.updated_at,j.error,j.error_code,
            CASE WHEN j.kind='identify' THEN l.title ELSE NULL END AS title
            FROM ai_jobs j LEFT JOIN listings l ON j.kind='identify' AND CAST(l.id AS TEXT)=j.target_id
            ORDER BY CASE WHEN j.status='running' THEN 0 WHEN j.error IS NOT NULL THEN 1 ELSE 2 END,j.updated_at DESC,j.id DESC LIMIT 10"""
        )
    ]


def job_summary(conn):
    counts = {key: 0 for key in ["queued", "running", "done", "failed", "cancelled"]}
    counts.update(
        {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status,count(*) AS n FROM ai_jobs GROUP BY status"
            )
        }
    )
    return counts
