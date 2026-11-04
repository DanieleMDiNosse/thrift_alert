# ruff: noqa: F811
"""Evidence and source-budget regression checks for the manual judgment."""

import pytest
from tests.test_ai_worth import conn, cfg, LLM, queued  # noqa: F401


def test_review_sources_cannot_starve_new_price_search(conn, cfg):
    from subitoo.ai import worth

    class ManyReviews:
        def __init__(self):
            self.fetched = []

        def search(self, query):
            if "prezzo nuovo" in query:
                return [{"url": "https://shop.example/new"}]
            return [{"url": f"https://review.example/{i}"} for i in range(5)]

        def fetch_page(self, url):
            self.fetched.append(url)
            return {
                "url": url,
                "title": "Evidence",
                "text": "Acme Box 2 standard nuovo disponibile: EUR 129,90.",
            }

    search = ManyReviews()
    _, payload = queued(conn, cfg)
    bundle = worth.analyze(conn, payload, LLM(), search=search, config=cfg)
    assert "https://shop.example/new" in [source["url"] for source in bundle["sources"]]
    assert len(bundle["sources"]) <= cfg.max_sources


@pytest.mark.parametrize(
    "quote,currency,amount",
    [
        ("Nuovo disponibile: 1.299,90 €", "EUR", 1299.9),
        ("Nuovo disponibile: EUR 1 299,90", "EUR", 1299.9),
        ("New in stock: USD 1,299.90", "USD", 1299.9),
    ],
)
def test_new_price_supports_grouped_currency_amounts(quote, currency, amount):
    from subitoo.ai.worth import _evidence

    result = {
        "new_price": {
            "source_id": "s",
            "quote": quote,
            "amount": amount,
            "currency": currency,
        }
    }
    _evidence(result, [{"id": "s", "text": quote}])


@pytest.mark.parametrize(
    "quote,currency,amount",
    [
        ("Prezzo 129,90 €", "USD", 129.9),
        ("Riduzione -129,90 EUR", "EUR", 129.9),
        ("Codice 129 e prezzo 90 EUR", "EUR", 129.0),
    ],
)
def test_price_amount_must_be_a_positive_currency_amount(quote, currency, amount):
    from subitoo.ai.worth import _evidence

    result = {
        "new_price": {
            "source_id": "s",
            "quote": quote,
            "amount": amount,
            "currency": currency,
        }
    }
    with pytest.raises(ValueError):
        _evidence(result, [{"id": "s", "text": quote}])
