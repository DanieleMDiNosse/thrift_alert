# ruff: noqa: F811
"""Actual marketplace field shapes, category policy and incomplete-cost views."""

import json
import pytest
from subitoo.ai import store
from subitoo.ai.schemas import validate_identity
from tests.test_ai_service import ai_conn, identity  # noqa: F401
from tests.test_ai_ranking import product, listing


@pytest.mark.parametrize("raw", ["{broken", "null", "[]"])
def test_malformed_marketplace_metadata_does_not_break_catalog(ai_conn, raw):
    ai_conn.execute("UPDATE listings SET raw_json=?", (raw,))
    assert store.listings(ai_conn)[0]["condition"] == "unknown"


def test_existing_subito_fields_are_not_discarded(ai_conn):
    raw = {
        "features": [
            {
                "uri": "/item_condition",
                "values": [
                    {"key": "20", "value": "Come nuovo - perfetto o ricondizionato"}
                ],
            },
            {
                "uri": "/item_shipping_cost_tuttosubito",
                "values": [{"key": "7,99", "value": "7,99 €"}],
            },
        ]
    }
    ai_conn.execute("UPDATE listings SET raw_json=?", (json.dumps(raw),))
    row = store.listings(ai_conn)[0]
    assert row["condition"] == "like_new"
    assert row["shipping_cost"] == 7.99
    assert row["fees"] is None
    assert row["condition_label"] == "Come nuovo - perfetto o ricondizionato"
    assert row["facts_sources"]["condition"] == "marketplace"


@pytest.mark.parametrize(
    "amount,currency,expected",
    [
        ("5.15", "EUR", 5.15),
        ("5.15", "USD", None),
        ("-3", "EUR", None),
        ("nan", "EUR", None),
    ],
)
def test_vinted_native_fee_and_condition(ai_conn, amount, currency, expected):
    ai_conn.execute("UPDATE queries SET site='vinted'")
    raw = {
        "serviceFee": {"amount": amount, "currencyCode": currency},
        "itemBox": {"secondLine": "Ottime"},
    }
    ai_conn.execute("UPDATE listings SET raw_json=?", (json.dumps(raw),))
    row = store.listings(ai_conn)[0]
    assert row["fees"] == expected
    assert row["condition"] == "good"
    assert row["shipping_cost"] is None


def test_manual_facts_keep_precedence(ai_conn):
    ai_conn.execute("UPDATE queries SET site='vinted'")
    ai_conn.execute(
        "UPDATE listings SET raw_json=?",
        (
            json.dumps(
                {
                    "itemBox": {"secondLine": "Ottime"},
                    "serviceFee": {"amount": "5.15", "currencyCode": "EUR"},
                }
            ),
        ),
    )
    ai_conn.execute(
        "INSERT INTO ai_marketplace_facts(site,site_listing_id,data_json,updated_at) VALUES ('vinted','abc',?,1)",
        (json.dumps({"condition": "poor", "fees": 0.0, "shipping_cost": None}),),
    )
    row = store.listings(ai_conn)[0]
    assert row["condition"] == "poor" and row["fees"] == 0
    assert row["facts_sources"]["condition"] == "user"
    assert row["shipping_cost"] is None


@pytest.mark.parametrize(
    "category", ["speakers", "speaker", "passive_bookshelf_speakers"]
)
def test_speaker_categories_share_audio_policy_without_classifying_fragrances(category):
    d = identity()
    d["category"] = category
    assert validate_identity(d)["category"] == "audio"
    d["category"] = "reed_diffuser"
    assert validate_identity(d)["category"] == "reed_diffuser"


def test_stored_category_alias_keeps_id_and_original_record(ai_conn):
    d = identity()
    d["category"] = "audio"
    pid = store.save_identity(ai_conn, 1, d)
    d["category"] = "speakers"
    ai_conn.execute(
        "UPDATE ai_products SET category=?,data_json=? WHERE id=?",
        ("speakers", json.dumps(d), pid),
    )
    p = store.products(ai_conn)[0]
    assert p["id"] == pid and p["category"] == "audio"
    assert p["original_category"] == "speakers"
    assert (
        json.loads(ai_conn.execute("SELECT data_json FROM ai_products").fetchone()[0])[
            "category"
        ]
        == "speakers"
    )


def test_incomplete_costs_have_separate_asking_price_indicator():
    from subitoo.ai.ranking import rank

    ad = listing(1, 100)
    ad["fees"] = None
    ad["shipping_cost"] = None
    result = rank([product()], [ad])
    row = result["listings"][0]
    assert row["value_score"] is None and row["effective_price"] is None
    assert row["asking_value_score"] is not None
    assert result["value"] == []
    assert result["asking_value"][0]["listing_id"] == 1
    assert row["value_basis"] == "asking_price_only"
