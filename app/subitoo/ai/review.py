"""Short, explicitly requested product reviews with fast web-snippet verification."""
import json
import os
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator
from . import worth

_UNKNOWN_NAMES = {
    "unknown",
    "unidentified",
    "n/a",
    "na",
    "none",
    "null",
    "?",
    "non identificabile",
    "non specificato",
    "non indicato",
    "non applicabile",
    "modello non specificato",
}


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Annotated[StrictStr, Field(min_length=1, max_length=600)]
    product_name: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None = None
    release_year: Annotated[StrictInt, Field(ge=1950, le=2035)] | None = None
    reference_price_eur: (
        Annotated[StrictFloat | StrictInt, Field(gt=0, le=500000, allow_inf_nan=False)] | None
    ) = None
    reference_price_kind: Literal["current_new", "launch_msrp"] | None = None
    drivetrain: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None = None
    crankset: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None = None
    lowest_gear_ratio: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None = None

    @field_validator("message")
    @classmethod
    def brief_plain_text(cls, value):
        value = value.strip()
        if not value or len(value.split()) > 60:
            raise ValueError("Review must contain 1–60 words")
        if re.search(r"(?:[a-z][a-z0-9+.-]*://|www\.)", value, re.I):
            raise ValueError("Generated URLs are forbidden")
        return value

    @field_validator("product_name", "drivetrain", "crankset", "lowest_gear_ratio")
    @classmethod
    def clean_product_name(cls, value):
        if value is None:
            return None
        value = " ".join(value.split())
        if not value or value.casefold() in _UNKNOWN_NAMES:
            return None
        if re.search(r"(?:[a-z][a-z0-9+.-]*://|www\.)", value, re.I):
            raise ValueError("Generated URLs are forbidden")
        return value

    @model_validator(mode="after")
    def normalize_reference_price(self):
        if self.reference_price_eur is None:
            self.reference_price_kind = None
        else:
            self.reference_price_eur = float(self.reference_price_eur)
            if self.reference_price_kind is None:
                self.reference_price_kind = "launch_msrp"
        if self.lowest_gear_ratio:
            m = re.search(r"\b(\d{2})\s*[/÷]\s*(\d{2})\s*[tT]?\b", self.lowest_gear_ratio)
            if m:
                front, rear = int(m.group(1)), int(m.group(2))
                if front > 0 and rear > 0:
                    ratio_str = f"{front / rear:.2f}".replace(".", ",")
                    if ratio_str not in self.lowest_gear_ratio and f"{front / rear:.2f}" not in self.lowest_gear_ratio:
                        self.lowest_gear_ratio = f"{self.lowest_gear_ratio} ({ratio_str})"[:120]
        return self


def read(conn, site, native_id, config=None):
    return worth.read(conn, site, native_id, config=config, purpose="review")


def request(conn, site, native_id, *, refresh=False, config=None):
    from .config import get_config

    cfg = config or get_config()
    if cfg.provider not in {"hermes", "openai"}:
        raise ValueError("Configura il provider GPT per generare recensioni con GPT-6.1-sol.")
    return worth.request(conn, site, native_id, refresh=refresh, config=cfg, purpose="review")


def fetch_web_snippets(title: str, description: str | None = None) -> list[dict]:
    """Fast DuckDuckGo snippet lookup (~1s) for specs, drivetrain, crankset and MSRP."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return []
    clean_title = " ".join(str(title or "").split())[:120]
    if not clean_title:
        return []
    query = f"{clean_title} specifiche scheda tecnica cambio guarnitura cassetta pignoni prezzo listino"
    try:
        from ddgs import DDGS

        raw_hits = list(DDGS(timeout=4).text(query, max_results=4) or [])
    except Exception:
        return []
    snippets = []
    seen = set()
    for hit in raw_hits:
        if not isinstance(hit, dict):
            continue
        url = str(hit.get("href") or hit.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        snippets.append(
            {
                "id": f"web_{len(snippets) + 1}",
                "title": " ".join(str(hit.get("title") or url).split())[:200],
                "url": url,
                "snippet": " ".join(str(hit.get("body") or hit.get("snippet") or "").split())[:350],
            }
        )
        if len(snippets) >= 4:
            break
    return snippets


def analyze(payload, llm, config, *, conn=None):
    listing = json.loads(worth._dump(payload["listing"]))
    if worth._fingerprint(listing) != payload["fingerprint"]:
        raise ValueError("Snapshot fingerprint mismatch")
    description = listing.get("description")
    if description is None and listing.get("site") == "vinted":
        try:
            from subitoo.core import db, descriptions

            url = listing.get("url")
            native_id = str(listing.get("site_listing_id") or "")
            if descriptions.can_load("vinted", native_id, url):
                fetched = descriptions.fetch_vinted_description(url, native_id)
                if isinstance(fetched, str) and fetched.strip():
                    description = fetched
                    if conn is not None:
                        listing["description"] = fetched
                        new_fp = worth._fingerprint(listing)
                        target = worth._key("vinted", native_id, "review")
                        conn.execute(
                            "INSERT OR REPLACE INTO listing_descriptions VALUES (?,?,?,?)",
                            ("vinted", native_id, fetched, db.now()),
                        )
                        conn.execute(
                            "UPDATE ai_listing_reviews SET request_fingerprint=?,snapshot_json=? WHERE target_id=? AND request_token=?",
                            (new_fp, worth._dump(listing), target, payload.get("request_token")),
                        )
                        payload["fingerprint"] = new_fp
        except Exception:
            pass
    snippets = fetch_web_snippets(listing.get("title", ""), description)
    # Product-only context: do not turn the review into a deal/price assessment.
    raw_context = {
        "listing": {
            "title": listing["title"],
            "description": description,
        }
    }
    if snippets:
        raw_context["web_snippets"] = [
            {"title": s["title"], "snippet": s["snippet"]} for s in snippets
        ]
    context = worth._bounded(raw_context, config.max_context_chars)
    result = Review.model_validate(llm.complete("review", context)).model_dump()
    sources = [
        {"id": s["id"], "title": s["title"], "url": s["url"]}
        for s in snippets
    ]
    result.update(source_ids=[s["id"] for s in sources], new_price=None)
    return dict(result=result, sources=sources, listing=listing)
