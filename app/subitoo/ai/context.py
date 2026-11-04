"""Budget database retrieval before inference; never truncate the latest question.

Older messages and evidence can be omitted from the prompt, never from persistence.
Context notes make this lossy retrieval explicit to the conversational model.
"""

from copy import deepcopy
import json


def build_chat_context(
    *, products, listings, messages, rankings, hypothetical, research_mode, max_chars
):
    def size(value):
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False))

    history = [{"role": m["role"], "content": m["content"]} for m in messages]
    latest = history[-1:]  # Persisted text stays intact, even when history is trimmed.
    context = {
        "research_mode": research_mode,
        "products": [],
        "listings": [],
        "messages": latest,
        "hypothetical": hypothetical,
        "rankings": {},
        "context_notes": [
            "Bounded retrieval: omitted history/evidence remains stored in the database. Never assume omitted evidence is absent."
        ],
    }
    if size(context) > max_chars:
        raise ValueError(
            "Newest question and price scenario exceed context budget; shorten the question"
        )

    # Preserve a bounded recent history with no duplicate DB fields/citation blobs.
    history_budget = min(max_chars // 3, 16000)
    used = sum(len(m["content"]) for m in latest)
    for message in reversed(history[:-1][-15:]):
        excerpt = {**message, "content": message["content"][-2000:]}
        if used + len(excerpt["content"]) > history_budget:
            break
        candidate = [excerpt] + context["messages"]
        if size({**context, "messages": candidate}) > max_chars:
            break
        context["messages"] = candidate
        used += len(excerpt["content"])

    # Product identities and a concise current synthesis have priority over old page
    # versions. Add only current references and the most recently retrieved sources.
    for product in products[:20]:
        research = product.get("research") or {}
        compact = {
            k: product.get(k)
            for k in [
                "id",
                "name",
                "category",
                "manufacturer",
                "model",
                "generation",
                "variant",
                "confidence",
                "research_status",
            ]
        }
        compact["research"] = {
            k: deepcopy(research.get(k))
            for k in [
                "summary",
                "strengths",
                "weaknesses",
                "known_issues",
                "confidence",
            ]
        }
        compact["research"]["summary"] = str(research.get("summary", ""))[:1500]
        compact["sources"] = []
        candidate = context["products"] + [compact]
        if size({**context, "products": candidate}) > max_chars:
            compact["research"] = {
                "summary": str(research.get("summary", ""))[:400],
                "confidence": research.get("confidence"),
            }
            if (
                size({**context, "products": context["products"] + [compact]})
                > max_chars
            ):
                continue
        context["products"].append(compact)

    # Lightweight ranking receipts, not repeated copies of every listing and policy.
    groups = rankings.get("by_category") or {"selected": rankings}
    receipts = {}
    for category, group in groups.items():
        receipts[category] = {}
        for kind in ["quality", "value", "deal"]:
            receipts[category][kind] = [
                {
                    k: row[k]
                    for k in [
                        "product_id",
                        "listing_id",
                        "quality_score",
                        "value_score",
                        "deal_score",
                        "confidence",
                        "effective_price",
                        "market",
                        "warnings",
                    ]
                    if k in row
                }
                for row in group.get(kind, [])[:5]
            ]
    if size({**context, "rankings": receipts}) <= max_chars:
        context["rankings"] = receipts

    for listing in listings[:100]:
        compact = {
            k: listing.get(k)
            for k in [
                "id",
                "product_id",
                "site",
                "title",
                "price",
                "currency",
                "condition",
                "shipping_cost",
                "fees",
                "accessories",
                "warranty",
            ]
        }
        if size({**context, "listings": context["listings"] + [compact]}) <= max_chars:
            context["listings"].append(compact)
        else:
            break

    for compact in context["products"]:
        original = next(p for p in products if p["id"] == compact["id"])
        cited = {
            sid
            for entry in (original.get("research") or {}).get("criteria", {}).values()
            for sid in entry.get("source_ids", [])
        }
        sources = sorted(
            original.get("sources", []),
            key=lambda s: (s["id"] in cited, s.get("retrieved_at", 0), s["id"]),
            reverse=True,
        )
        for source in sources[:5]:
            excerpt = {
                k: source.get(k)
                for k in ["id", "url", "title", "kind", "reliability", "retrieved_at"]
            }
            excerpt["text"] = source.get("text", "")[:1200]
            compact["sources"].append(excerpt)
            if size(context) > max_chars:
                compact["sources"].pop()
        available = {s["id"] for s in compact["sources"]}
        criteria = {
            k: {
                **entry,
                "source_ids": [sid for sid in entry["source_ids"] if sid in available],
            }
            for k, entry in (original.get("research") or {}).get("criteria", {}).items()
            if set(entry.get("source_ids", [])) & available
        }
        compact["research"]["criteria"] = criteria
        if size(context) > max_chars:
            compact["research"].pop("criteria")
    assert size(context) <= max_chars
    return context
