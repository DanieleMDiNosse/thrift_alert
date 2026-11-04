import pytest
from subitoo.ai import ranking as r


def product(identity="p", category="camera"):
    return {
        "id": identity,
        "category": category,
        "variant": "body only",
        "confidence": 0.9,
        "research": {
            "confidence": 0.9,
            "criteria": {
                k: {"score": 80, "source_ids": ["s"]}
                for k in r.DEFAULT_WEIGHTS[category]
            },
        },
    }


def listing(i, price=500, **kw):
    return {
        "id": i,
        "product_id": "p",
        "site": "subito",
        "site_listing_id": str(i),
        "price": price,
        "currency": "EUR",
        "condition": "good",
        "shipping_cost": 0,
        "fees": 0,
        "confidence": 0.9,
        **kw,
    }


def test_separate_rankings_and_hypothetical_are_reproducible():
    ps = [product()]
    ls = [listing(i, 500 + i * 10) for i in range(7)]
    ls.append(listing(10, 300))
    a = r.rank(ps, ls)
    assert a == r.rank(list(reversed(ps)), list(reversed(ls)))
    assert a["quality"][0]["quality_score"] == pytest.approx(80)
    assert a["value"][0]["listing_id"] == 10
    assert a["deal"][0]["listing_id"] == 10
    b = r.rank(ps, ls, hypothetical={"10": 900})
    assert b["value"][0]["listing_id"] != 10
    assert ls[-1]["price"] == 300


def test_insufficient_samples_unknown_costs_and_conditions():
    row = r.rank([product()], [listing(1, shipping_cost=None, condition="unknown")])[
        "listings"
    ][0]
    assert row["effective_price"] is None
    assert row["known_total"] == 500
    assert row["value_score"] is None and row["deal_score"] is None
    assert row["market"]["median"] is None
    assert row["warnings"]


def test_comparables_exclude_duplicate_ids_currencies_conditions_and_bundles():
    ls = [listing(i) for i in range(6)]
    ls += [
        listing(0),
        listing(22, currency="USD"),
        listing(23, condition="poor"),
        listing(24, variant="lens bundle"),
    ]
    result = r.rank([product()], ls)
    row = next(x for x in result["listings"] if x["listing_id"] == 1)
    assert row["market"]["n"] == 5
    assert row["market"]["kind"] == "asking"


def test_missing_criteria_dont_become_full_quality_and_categories_separate():
    p = product()
    p["research"]["criteria"] = {}
    assert r.rank([p], [listing(1)])["quality"] == []
    with pytest.raises(ValueError):
        r.rank([product(), product("a", "audio")], [])


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -1, True, 0])
def test_invalid_prices_not_ranked(price):
    assert r.rank([product()], [listing(1, price)])["value"] == []


def test_weights_validate_and_no_sources_no_quality():
    with pytest.raises(ValueError):
        r.rank([product()], [], weights={"camera": {"autofocus": -1}})
    p = product()
    for criterion in p["research"]["criteria"].values():
        criterion["source_ids"] = []
    assert not r.rank([p], [])["quality"]
