"""Standalone optional worker: python -m subitoo.ai.worker --db PATH.

No model-server lifecycle or arbitrary tools. One process per DB (flock), one
inference request at a time (LLMClient lock), bounded durable retry queue.
"""

import argparse
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import signal
import sqlite3
import time
from urllib.parse import urlsplit
from . import store
from .config import get_config
from .llm import LLMClient, InferenceUnavailable, InferenceError, ERROR_MESSAGES
from .ranking import DEFAULT_WEIGHTS
from .schemas import validate_identity, validate_research, validate_chat

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, conn, config=None, llm=None, search=None):
        self.conn = conn
        self.config = config or get_config()
        self.llm = llm or LLMClient(self.config)
        self.search = search

    def recover_stale(self, *, recover_all=False):
        if recover_all:
            if not self.conn.execute(
                "SELECT 1 FROM ai_jobs WHERE status='running' LIMIT 1"
            ).fetchone():
                return
            # Only call at startup AFTER acquiring the exclusive per-DB flock.
            # No previous worker can still own these claims; interruption is not
            # a malformed-output failure and should not consume its retry budget.
            self.conn.execute(
                "UPDATE ai_jobs SET status='queued',attempts=MAX(0,attempts-1),available_at=?,updated_at=?,error='Attività interrotta: ripresa programmata.',error_code='interrupted' WHERE status='running'",
                (store.now(), store.now()),
            )
            return
        cutoff = store.now() - self.config.stale_seconds
        if not self.conn.execute(
            "SELECT 1 FROM ai_jobs WHERE status='running' AND updated_at<? LIMIT 1",
            (cutoff,),
        ).fetchone():
            return
        self.conn.execute(
            """UPDATE ai_jobs SET status=CASE WHEN attempts>=? THEN 'failed' ELSE 'queued' END,
            available_at=?,updated_at=?,error='Interrupted work recovered'
            WHERE status='running' AND updated_at<?""",
            (self.config.max_attempts, store.now(), store.now(), cutoff),
        )

    def run_once(self):
        if self.config.configuration_error():
            return False
        # An explicit local button click requests one judgment even when the old
        # autonomous workflow is paused. Never drain legacy work in manual mode.
        only_worth = (
            self.config.workflow == "on_demand"
            or not store.settings(self.conn, self.config)["enabled"]
        )
        ts = store.now()
        if not self.conn.execute(
            "SELECT 1 FROM ai_jobs WHERE status='queued' AND available_at<=? AND attempts<? AND (?=0 OR kind='worth') LIMIT 1",
            (ts, self.config.max_attempts, int(only_worth)),
        ).fetchone():
            return False
        with store.atomic(self.conn):
            row = self.conn.execute(
                "SELECT * FROM ai_jobs WHERE status='queued' AND available_at<=? AND attempts<? AND (?=0 OR kind='worth') ORDER BY CASE kind WHEN 'worth' THEN -1 WHEN 'chat' THEN 0 WHEN 'research' THEN 1 ELSE 2 END,priority DESC,id LIMIT 1",
                (ts, self.config.max_attempts, int(only_worth)),
            ).fetchone()
            if row is None:
                return False
            changed = self.conn.execute(
                "UPDATE ai_jobs SET status='running',attempts=attempts+1,updated_at=?,error=NULL,error_code=NULL WHERE id=? AND status='queued'",
                (ts, row["id"]),
            ).rowcount
            if not changed:
                return False
        job = dict(row)
        job["payload"] = json.loads(job["payload_json"])
        try:
            if self.conn.in_transaction:
                raise RuntimeError("Network cannot run in an open transaction")
            result = self._process(job)
            with store.atomic(self.conn):
                current = self.conn.execute(
                    "SELECT status FROM ai_jobs WHERE id=?", (job["id"],)
                ).fetchone()
                if current["status"] != "running":
                    return True
                # Atomically release this successful job's capacity before follow-up
                # work is enqueued. Rollback restores its running state on failure.
                self.conn.execute(
                    "UPDATE ai_jobs SET status='done',updated_at=? WHERE id=?",
                    (store.now(), job["id"]),
                )
                if job["kind"] == "worth":
                    from .worth import save

                    save(self.conn, job["target_id"], job["payload"], result)
                elif job["kind"] == "identify":
                    pid = store.save_identity(self.conn, int(job["target_id"]), result)
                    product = self.conn.execute(
                        "SELECT research_status FROM ai_products WHERE id=?", (pid,)
                    ).fetchone()
                    if product["research_status"] != "ready":
                        store.enqueue(self.conn, "research", pid, config=self.config)
                elif job["kind"] == "research":
                    research, evidence = result
                    store.save_research(self.conn, job["target_id"], research, evidence)
                elif job["kind"] == "chat":
                    conv = self.conn.execute(
                        "SELECT revision FROM ai_conversations WHERE id=?",
                        (job["target_id"],),
                    ).fetchone()
                    if conv and conv["revision"] == job["payload"]["revision"]:
                        self.conn.execute(
                            "INSERT OR IGNORE INTO ai_messages(conversation_id,role,message,source_ids_json,created_at,job_id) VALUES(?,'assistant',?,?,?,?)",
                            (
                                job["target_id"],
                                result["message"],
                                store.dumps(result["source_ids"]),
                                store.now(),
                                job["id"],
                            ),
                        )
                        self.conn.execute(
                            "UPDATE ai_conversations SET updated_at=? WHERE id=?",
                            (store.now(), job["target_id"]),
                        )
                self.conn.execute(
                    "UPDATE ai_jobs SET status='done',updated_at=? WHERE id=?",
                    (store.now(), job["id"]),
                )
        except InferenceUnavailable as error:
            self.conn.execute(
                "UPDATE ai_jobs SET status='queued',attempts=?,available_at=?,updated_at=?,error=?,error_code=? WHERE id=? AND status='running'",
                (
                    job["attempts"],
                    store.now() + 60,
                    store.now(),
                    str(error),
                    error.code,
                    job["id"],
                ),
            )
        except Exception as error:
            # Never persist exception strings: HTTP errors may contain keys, URLs or user data.
            attempts = job["attempts"] + 1
            status = "failed" if attempts >= self.config.max_attempts else "queued"
            code = error.code if isinstance(error, InferenceError) else "task_failed"
            message = ERROR_MESSAGES.get(
                code, "Operazione AI non riuscita; riprova dal pannello attività."
            )
            with store.atomic(self.conn):
                self.conn.execute(
                    "UPDATE ai_jobs SET status=?,available_at=?,updated_at=?,error=?,error_code=? WHERE id=? AND status='running'",
                    (
                        status,
                        store.now() + min(300, 5 * 2 ** (attempts - 1)),
                        store.now(),
                        message,
                        code,
                        job["id"],
                    ),
                )
                if status == "failed" and job["kind"] == "research":
                    self.conn.execute(
                        "UPDATE ai_products SET research_status='failed',updated_at=? WHERE id=?",
                        (store.now(), job["target_id"]),
                    )
            log.warning("AI job %s failed (%s; %s)", job["id"], status, code)
        return True

    def _process(self, job):
        kind = job["kind"]
        target = job["target_id"]
        if kind == "worth":
            if job["payload"].get("purpose") == "review":
                from .review import analyze

                return analyze(job["payload"], self.llm, self.config, conn=self.conn)
            from .worth import analyze

            return analyze(
                self.conn,
                job["payload"],
                self.llm,
                search=self.search,
                config=self.config,
            )
        if kind == "identify":
            row = self.conn.execute(
                "SELECT l.title,l.raw_json,l.site_listing_id,q.site FROM listings l JOIN queries q ON q.id=l.query_id WHERE l.id=?",
                (target,),
            ).fetchone()
            if row is None:
                raise ValueError("Listing no longer exists")
            previous = self.conn.execute(
                "SELECT p.data_json FROM ai_listing_products a JOIN listings l ON l.id=a.listing_id JOIN queries q ON q.id=l.query_id JOIN ai_products p ON p.id=a.product_id WHERE q.site=? AND l.site_listing_id=? ORDER BY l.last_seen_at DESC,l.id DESC LIMIT 1",
                (row["site"], row["site_listing_id"]),
            ).fetchone()
            if previous:
                return validate_identity(json.loads(previous["data_json"]))
            try:
                raw = json.loads(row["raw_json"] or "{}")
                description = raw.get("body") or raw.get("description")
            except (TypeError, ValueError, AttributeError):
                description = None
            cached = self.conn.execute(
                "SELECT description FROM listing_descriptions WHERE site=? AND site_listing_id=?",
                (row["site"], row["site_listing_id"]),
            ).fetchone()
            if cached:
                description = cached["description"]
            context = {
                "title": row["title"][:500],
                "site": row["site"],
                "description": description[:1800]
                if isinstance(description, str)
                else None,
                "categories": list(DEFAULT_WEIGHTS),
                "instructions": "Select canonical category when applicable; null unknown generation/variant.",
            }
            return validate_identity(self.llm.complete("identity", context))
        if kind == "research":
            product = next(
                (p for p in store.products(self.conn) if p["id"] == target), None
            )
            if not product:
                raise ValueError("Product not found")
            return self.research(product)
        if kind == "chat":
            conv = self.conn.execute(
                "SELECT * FROM ai_conversations WHERE id=?", (target,)
            ).fetchone()
            if not conv or conv["revision"] != job["payload"]["revision"]:
                raise ValueError("Conversation changed")
            ids = json.loads(conv["product_ids_json"])
            products = store.products(self.conn)
            if ids:
                products = [p for p in products if p["id"] in ids]
            elif conv["watch_id"] is not None:
                visible = {
                    listing["product_id"]
                    for listing in store.listings(self.conn, conv["watch_id"])
                }
                products = [p for p in products if p["id"] in visible]
            products = products[:20]
            if job["payload"].get("research"):
                # Research is explicit; ordinary chat never performs a fresh web search.
                for product in products:
                    question = store.messages(self.conn, target)[-1]["content"]
                    research, evidence = self.research(product, question=question)
                    with store.atomic(self.conn):
                        store.save_research(
                            self.conn,
                            product["id"],
                            research,
                            evidence,
                            supplement=True,
                        )
                products = [
                    p
                    for p in store.products(self.conn)
                    if p["id"] in {x["id"] for x in products}
                ]
            for p in products:
                for s in p["sources"]:
                    s["text"] = s["text"][:1500]
            from .service import grouped_rankings

            listings = [
                listing
                for listing in store.listings(self.conn, conv["watch_id"])
                if listing["product_id"] in {p["id"] for p in products}
            ][:100]
            hypothetical = job["payload"].get("hypothetical", {})
            from .context import build_chat_context

            context = build_chat_context(
                research_mode="new web search"
                if job["payload"].get("research")
                else "cached research only",
                products=products,
                listings=listings,
                messages=store.messages(self.conn, target),
                hypothetical=hypothetical,
                rankings=grouped_rankings(
                    products,
                    listings,
                    store.settings(self.conn, self.config)["weights"],
                    hypothetical,
                ),
                max_chars=self.config.max_context_chars,
            )
            return validate_chat(
                self.llm.complete("chat", context),
                {s["id"] for p in context["products"] for s in p["sources"]},
            )
        raise ValueError("Unknown job type")

    def research(self, product, question=None):
        if self.search is None:
            from .network import SearchBackend

            self.search = SearchBackend(self.config)
        query = " ".join(
            str(product.get(k) or "")
            for k in ["manufacturer", "name", "model", "generation", "variant"]
        ).strip()
        suffix = question[:300] if question else "review specifications known issues"
        if question and "reddit" in question.casefold():
            suffix += " site:reddit.com"
        results = self.search.search((query[:180] + " " + suffix)[:500])
        evidence = []
        seen = set()
        for hit in results[: self.config.search_limit]:
            url = store.canonical_url(hit.get("url"))
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                if hasattr(self.search, "fetch_page"):
                    page = self.search.fetch_page(url)
                else:
                    from .network import fetch_page

                    page = fetch_page(url)
                final = store.canonical_url(page["url"])
                text = page.get("text", "").strip()
                if not final or not text:
                    continue
                host = urlsplit(final).hostname or ""
                kind = (
                    "forum"
                    if any(x in host for x in ["reddit.com", "forum."])
                    else "web"
                )
                # A page is evidence of its claims, not an independently measured failure rate.
                evidence.append(
                    {
                        "id": hashlib.sha256(
                            (product["id"] + "|" + final + "|" + text[:5000]).encode()
                        ).hexdigest()[:32],
                        "url": final,
                        "title": str(page.get("title", ""))[:500],
                        "text": text[:5000],
                        "retrieved_at": int(page.get("retrieved_at") or store.now()),
                        "publication_date": page.get("publication_date"),
                        "kind": kind,
                        "reliability": "unassessed; anecdotal claims are not failure-rate estimates"
                        if kind == "forum"
                        else "unassessed; publisher claims require corroboration",
                    }
                )
            except Exception:
                continue
            if len(evidence) >= self.config.max_sources:
                break
        # No synthetic knowledge fallback when retrieval fails.
        if not evidence:
            raise ValueError("No usable retrieved evidence")
        clean = {k: v for k, v in product.items() if k not in {"sources", "research"}}
        criteria = list(DEFAULT_WEIGHTS.get(product["category"], {}))
        context = {
            "product": clean,
            "category_criteria": criteria,
            "sources": evidence,
            "evidence_policy": "Use category criteria. Do not assert independent failure rates from forum anecdotes or manufacturer advertising. Omit unsupported numeric scores; preserve missing evidence and qualified reliability.",
        }
        research = validate_research(
            self.llm.complete("research", context), {s["id"] for s in evidence}
        )
        for source in evidence:
            assessment = research.get("source_assessments", {}).get(source["id"])
            if assessment:
                source["kind"] = assessment["source_type"]
                source["reliability"] = (
                    "Model-assessed "
                    + assessment["reliability"]
                    + ": "
                    + assessment["rationale"]
                )
        if len({urlsplit(s["url"]).hostname for s in evidence}) < 2:
            research["confidence"] = min(research["confidence"], 0.5)
        if criteria and not set(research["criteria"]) <= set(criteria):
            raise ValueError("Unknown category criterion")
        return research, evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    from subitoo.core import db
    from subitoo.config import get_settings

    path = args.db or get_settings().db_path
    if path == ":memory:":
        raise SystemExit("Worker requires a persistent database")
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(Path(path).expanduser().resolve()) + ".ai-worker.lock")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("AI worker already running") from None
        conn = db.init_db(path)
        cfg = get_config()
        worker = Worker(conn, cfg)
        stopped = False

        def stop(*_):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        last_discovery = 0
        try:
            try:
                worker.recover_stale(recover_all=True)
            except sqlite3.OperationalError:
                log.warning("AI worker startup recovery deferred: database busy")
            while not stopped:
                try:
                    worker.recover_stale()
                    if (
                        cfg.workflow == "legacy"
                        and store.settings(conn, cfg)["enabled"]
                        and time.monotonic() - last_discovery >= cfg.discover_seconds
                    ):
                        try:
                            store.discover(conn, cfg, limit=cfg.discovery_batch_size)
                        except ValueError:
                            log.warning("AI discovery deferred: queue full")
                        last_discovery = time.monotonic()
                    worked = worker.run_once()
                except sqlite3.OperationalError:
                    log.warning("AI worker tick deferred: database busy")
                    worked = False
                except Exception:
                    if args.once:
                        raise
                    log.exception("AI worker unexpected error during tick; continuing")
                    worked = False
                if args.once:
                    break
                if not worked:
                    time.sleep(cfg.poll_seconds)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
