"""Optional manual smoke: isolated synthetic listing, real local inference/search.

Does not touch the production DB or contact marketplaces. Print only public evidence
and outcome, not configuration or exception strings. Exit nonzero on failure.
"""

import json
import os
from pathlib import Path
import tempfile
import time

from subitoo.ai import service, store
from subitoo.ai.config import AIConfig
from subitoo.ai.worker import Worker
from subitoo.core import db
from subitoo.core.models import Filters, Listing


def main():
    root = Path(os.environ.get("TMPDIR", ".")).resolve()
    with tempfile.TemporaryDirectory(
        prefix="marketwatch-ai-smoke-", dir=root
    ) as folder:
        conn = db.init_db(str(Path(folder) / "smoke.sqlite"))
        query = db.create_query(
            conn,
            name="Synthetic AI smoke, not a real offer",
            site="subito",
            search={},
            filters=Filters(),
            cron="*/30 * * * *",
        )
        db.insert_listing(
            conn,
            query,
            Listing(
                site_listing_id="synthetic-smoke",
                title="Sony A6400 corpo macchina senza obiettivo",
                price=450,
            ),
        )
        service.action(conn, "/api/ai/settings", {"enabled": True})
        service.action(conn, "/api/ai/sync", {})
        config = AIConfig(
            max_sources=2,
            search_limit=4,
            max_tokens=2500,
            retries=0,
            timeout=300,
            lock_path=str(Path(folder) / "inference.lock"),
        )
        worker = Worker(conn, config=config)
        try:
            for phase in ["identification", "research"]:
                started = time.monotonic()
                worker.run_once()
                state = service.snapshot(conn)
                report = {
                    "phase": phase,
                    "seconds": round(time.monotonic() - started, 2),
                    "jobs": [
                        {"kind": j["kind"], "status": j["status"]}
                        for j in state["jobs"]
                    ],
                }
                if phase == "identification":
                    report["products"] = [
                        {"model": p["model"], "variant": p["variant"]}
                        for p in state["products"]
                    ]
                    print(json.dumps(report), flush=True)
                    if not state["products"]:
                        return 1
                else:
                    product = state["products"][0]
                    report["research_status"] = product["research_status"]
                    report["sources"] = [
                        {"url": s["url"], "kind": s["kind"]} for s in product["sources"]
                    ]
                    report["criteria"] = product["research"].get("criteria", {})
                    print(json.dumps(report), flush=True)
                    if not product["sources"] or not product["research"]:
                        return 2
            conversation = service.action(
                conn, "/api/ai/conversations/new", {"product_ids": [product["id"]]}
            )["id"]
            service.action(
                conn,
                f"/api/ai/conversations/{conversation}/messages",
                {
                    "message": "Rispondi in italiano: quali sono i compromessi per bikepacking? Usa solo le fonti disponibili."
                },
            )
            worker.run_once()
            messages = store.messages(conn, conversation)
            ok = len(messages) == 2 and messages[-1]["role"] == "assistant"
            print(
                json.dumps(
                    {
                        "phase": "chat",
                        "completed": ok,
                        "reply": messages[-1]["content"] if ok else None,
                        "citations": messages[-1]["source_ids"] if ok else [],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0 if ok else 3
        finally:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
