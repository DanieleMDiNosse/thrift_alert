"""Deterministic, category-local heuristic scores; never transaction valuations.

Quality is a weighted evidence-supported rubric (0–100). Missing criteria contribute
zero; coverage <50% is ineligible. Value is risk-adjusted quality points per EUR100
of *known complete* cost. Deal is percent discount against >=5 other distinct,
recent, same-product/variant/condition/currency asking prices. No sale inference.
"""

from __future__ import annotations

import math

import time

DEFAULT_WEIGHTS = {
    "camera": {
        "image_quality": 0.30,
        "autofocus": 0.20,
        "stabilization": 0.15,
        "portability": 0.10,
        "battery": 0.10,
        "reliability": 0.15,
    },
    "audio": {
        "sound_quality": 0.30,
        "power": 0.15,
        "connectivity": 0.15,
        "build_quality": 0.15,
        "repairability": 0.10,
        "reliability": 0.15,
    },
    "bicycle": {
        "frame": 0.25,
        "groupset": 0.20,
        "braking": 0.20,
        "weight": 0.10,
        "durability": 0.15,
        "upgradeability": 0.10,
    },
    "computer": {
        "performance": 0.30,
        "memory": 0.15,
        "storage": 0.15,
        "display": 0.15,
        "battery": 0.10,
        "reliability": 0.15,
    },
    "phone": {
        "performance": 0.20,
        "camera": 0.20,
        "battery": 0.20,
        "display": 0.15,
        "software_support": 0.15,
        "reliability": 0.10,
    },
}
CONDITION_FACTORS = {
    "new": 1.0,
    "like_new": 0.98,
    "good": 0.90,
    "fair": 0.75,
    "poor": 0.5,
    "unknown": 0.6,
}
POLICY_VERSION = "heuristic-v1"


def number(value, *, positive=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
    ):
        return None
    if value < 0 or (positive and value == 0):
        return None
    return float(value)


def _quality(product, weights):
    research = product.get("research") or {}
    criteria = research.get("criteria") or {}
    numerator = coverage = 0.0
    for name, weight in weights.items():
        entry = criteria.get(name) or {}
        score = number(entry.get("score"))
        if score is not None and score <= 100 and entry.get("source_ids"):
            numerator += score * weight
            coverage += weight
    total = sum(weights.values())
    return (
        round(numerator / total, 6) if coverage / total >= 0.5 else None,
        round(coverage / total, 6),
    )


def _quantile(sorted_values, fraction):
    """Inclusive linear quantile without adding large positive prices together."""
    position = (len(sorted_values) - 1) * fraction
    left = math.floor(position)
    right = math.ceil(position)
    a, b = sorted_values[left], sorted_values[right]
    return a + (b - a) * (position - left)


def rank(products, listings, weights=None, hypothetical=None):
    """Pure deterministic calculation (apart from freshness cutoff at current time).

    Input records are never mutated. Mixed categories must be filtered by caller.
    Unknown costs are not assumed zero. Product-level variant identity is required;
    explicit listing variant overrides are respected as extra comparability guards.
    """
    categories = {p["category"] for p in products}
    if len(categories) > 1:
        raise ValueError("Select one product category before ranking.")
    policies = {k: dict(v) for k, v in DEFAULT_WEIGHTS.items()}
    for category, custom in (weights or {}).items():
        if category not in policies or not isinstance(custom, dict) or not custom:
            raise ValueError("Invalid ranking category/weights")
        if any(
            k not in policies[category] or number(v) is None for k, v in custom.items()
        ):
            raise ValueError("Invalid ranking weight")
        scale = max(custom.values())
        if scale <= 0:
            raise ValueError("Weights must have positive sum")
        # Scaling before summation avoids overflow for finite huge preferences.
        scaled = {key: value / scale for key, value in custom.items()}
        total = math.fsum(scaled.values())
        policies[category] = {key: value / total for key, value in scaled.items()}
    by_id = {p["id"]: p for p in products}
    scores = {}
    quality = []
    for p in products:
        rubric = policies.get(p["category"])
        q, coverage = _quality(p, rubric) if rubric else (None, 0.0)
        confidence = min(
            number(p.get("confidence")) or 0.0,
            number((p.get("research") or {}).get("confidence")) or 0.0,
            coverage,
            1.0,
        )
        scores[p["id"]] = (q, confidence)
        if q is not None:
            quality.append(
                {
                    "product_id": p["id"],
                    "name": p.get("name", p["id"]),
                    "quality_score": q,
                    "confidence": confidence,
                    "coverage": coverage,
                }
            )
    unique = {}
    for item in sorted(listings, key=lambda x: (x.get("last_seen_at", 0), x["id"])):
        key = (item.get("site"), item.get("site_listing_id", str(item["id"])))
        unique[key] = dict(item)
    rows = list(unique.values())
    hypothetical = hypothetical or {}
    if len(hypothetical) > 100:
        raise ValueError("Too many hypothetical prices")
    for key, value in hypothetical.items():
        if number(value, positive=True) is None:
            raise ValueError("Hypothetical prices must be finite positive numbers")
    for item in rows:
        item["observed_price"] = item.get("price")
        item["price"] = hypothetical.get(
            str(item["id"]), hypothetical.get(item["id"], item.get("price"))
        )
    result = []
    cutoff = int(time.time()) - 90 * 86400
    for item in rows:
        p = by_id.get(item.get("product_id"))
        if p is None:
            continue
        q, confidence = scores[p["id"]]
        price = number(item.get("price"), positive=True)
        shipping, fees = number(item.get("shipping_cost")), number(item.get("fees"))
        known_total = None if price is None else price + (shipping or 0) + (fees or 0)
        if known_total is not None and not math.isfinite(known_total):
            known_total = None
        effective = known_total if shipping is not None and fees is not None else None
        condition = item.get("condition") or "unknown"
        warnings = []
        if shipping is None:
            warnings.append("Shipping cost unknown")
        if fees is None:
            warnings.append("Marketplace fees unknown")
        if condition == "unknown":
            warnings.append("Condition unknown; conservative penalty")
        if price is None:
            warnings.append("Valid positive asking price unavailable")
        variant = item.get("variant", p.get("variant"))
        comparable = []
        if (
            condition != "unknown"
            and variant
            and item.get("last_seen_at", cutoff) >= cutoff
        ):
            for other in rows:
                if (
                    other["id"] != item["id"]
                    and other.get("product_id") == p["id"]
                    and other.get("currency") == item.get("currency")
                    and other.get("condition") == condition
                    and other.get("variant", p.get("variant")) == variant
                    and other.get("last_seen_at", cutoff) >= cutoff
                ):
                    v = number(other.get("observed_price"), positive=True)
                    if v is not None:
                        comparable.append(v)
        n = len(comparable)
        comparable.sort()
        median = _quantile(comparable, 0.5) if n >= 5 else None
        iqr = None
        if median is not None:
            iqr = [_quantile(comparable, 0.25), _quantile(comparable, 0.75)]
        else:
            warnings.append(
                "Insufficient comparable asking prices (minimum 5 other listings)"
            )
        value = (
            q * CONDITION_FACTORS.get(condition, 0.6) * 100 / effective
            if q is not None and effective is not None and effective > 0
            else None
        )
        asking_value = (
            q * CONDITION_FACTORS.get(condition, 0.6) * 100 / price
            if q is not None and price is not None
            else None
        )
        if asking_value is not None and not math.isfinite(asking_value):
            asking_value = None
        deal = 100 * (1 - price / median) if median and price is not None else None
        if value is not None and not math.isfinite(value):
            value = None
            warnings.append("Value calculation outside representable numeric range")
        if deal is not None and not math.isfinite(deal):
            deal = None
            warnings.append("Deal calculation outside representable numeric range")
        result.append(
            {
                **item,
                "listing_id": item["id"],
                "name": p.get("name", p["id"]),
                "quality_score": q,
                "value_score": None if value is None else round(value, 6),
                "asking_value_score": None
                if asking_value is None
                else round(asking_value, 6),
                "value_basis": "complete_cost"
                if effective is not None
                else "asking_price_only",
                "deal_score": None if deal is None else round(deal, 6),
                "known_total": known_total,
                "effective_price": effective,
                "condition_factor": CONDITION_FACTORS.get(condition, 0.6),
                "confidence": min(confidence, 0.5 if warnings else 1.0),
                "warnings": warnings,
                "market": {
                    "kind": "asking",
                    "n": n,
                    "median": median,
                    "iqr": iqr,
                    "confidence": "limited" if median is not None else "insufficient",
                },
                "policy_version": POLICY_VERSION,
            }
        )

    def ordered(key):
        return sorted(
            (x for x in result if x[key] is not None),
            key=lambda x: (-x[key], x["listing_id"]),
        )

    # Do not compare value indices across currencies.
    currencies = {x.get("currency") for x in result if x.get("value_score") is not None}
    asking_currencies = {
        x.get("currency") for x in result if x.get("asking_value_score") is not None
    }
    return {
        "quality": sorted(
            quality, key=lambda x: (-x["quality_score"], x["product_id"])
        ),
        "value": ordered("value_score") if len(currencies) <= 1 else [],
        "asking_value": ordered("asking_value_score")
        if len(asking_currencies) <= 1
        else [],
        "deal": ordered("deal_score"),
        "listings": sorted(result, key=lambda x: x["listing_id"]),
        "policy_version": POLICY_VERSION,
        "weights": policies,
    }
