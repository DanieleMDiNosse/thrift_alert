"""Read explicit marketplace fields only; no LLM inference or guessed fees.

Condition is a seller/platform declaration, not an independent inspection. Raw
labels are preserved. Missing charges remain unknown, including local pickup.
"""

import json
import math
import re


def _amount(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not re.fullmatch(r"\d+(?:[.,]\d{1,2})?", value.strip()):
            return None
        value = float(value.strip().replace(",", "."))
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def marketplace_facts(site, raw, currency):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    result = {
        "condition": "unknown",
        "shipping_cost": None,
        "fees": None,
        "condition_label": None,
        "facts_sources": {},
    }
    if not isinstance(raw, dict):
        return result
    label = None
    if site == "subito":
        features = raw.get("features")
        for feature in features if isinstance(features, list) else []:
            if not isinstance(feature, dict):
                continue
            values = feature.get("values")
            first = values[0] if isinstance(values, list) and values else {}
            if not isinstance(first, dict):
                continue
            if feature.get("uri") == "/item_condition":
                label = first.get("value")
            elif (
                feature.get("uri") == "/item_shipping_cost_tuttosubito"
                and currency == "EUR"
            ):
                result["shipping_cost"] = _amount(first.get("key"))
    elif site == "vinted":
        box = raw.get("itemBox")
        if isinstance(box, dict):
            label = box.get("secondLine")
        fee = raw.get("serviceFee")
        if isinstance(fee, dict) and fee.get("currencyCode") == currency:
            result["fees"] = _amount(fee.get("amount"))
    # Exact observed platform labels. Never infer condition from arbitrary ad text.
    labels = {
        "come nuovo - perfetto o ricondizionato": "like_new",
        "nuovo - mai usato in confezione originale": "new",
        "ottimo - poco usato e ben conservato": "good",
        "buono - usato ma ben conservato": "good",
        "danneggiato - usato con parti guaste": "poor",
        "nuovo con cartellino": "new",
        "nuovo senza cartellino": "new",
        "ottime": "good",
        "buone": "good",
        "discrete": "fair",
    }
    if isinstance(label, str):
        result["condition_label"] = label[:200]
        result["condition"] = labels.get(label.strip().casefold(), "unknown")
    for key in ("condition", "shipping_cost", "fees"):
        if result[key] is not None and result[key] != "unknown":
            result["facts_sources"][key] = "marketplace"
    return result
