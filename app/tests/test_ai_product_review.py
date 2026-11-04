"""Brief product reviews are explicit, isolated from legacy deal judgments."""
import json
import pytest
from tests.test_ai_worth import conn  # noqa: F401
from tests.test_ai_hermes_provider import config


def test_review_queue_cache_and_worker(conn, tmp_path):
    from subitoo.ai import review, worth
    from subitoo.ai.worker import Worker
    cfg = config(tmp_path)
    legacy = worth.request(conn, 'subito', 'abc', config=cfg)
    before = conn.total_changes
    assert review.read(conn, 'subito', 'abc', config=cfg)['status'] == 'not_analyzed'
    assert conn.total_changes == before
    state = review.request(conn, 'subito', 'abc', config=cfg)
    assert state['job_id'] != legacy['job_id']
    assert review.request(conn, 'subito', 'abc', config=cfg)['job_id'] == state['job_id']
    # Leave legacy work untouched in this test.
    conn.execute("UPDATE ai_jobs SET status='failed' WHERE id=?", (legacy['job_id'],))
    calls = []
    class Model:
        def complete(self, task, context):
            calls.append((task, context))
            return {'message': 'Prodotto compatto, adatto a un uso semplice. Il limite principale è la versatilità ridotta.'}
    assert Worker(conn, cfg, llm=Model()).run_once()
    result = review.read(conn, 'subito', 'abc', config=cfg)
    assert result['status'] == 'completed'
    assert result['result']['new_price'] is None and result['sources'] == []
    assert len(calls) == 1 and calls[0][0] == 'review'
    assert 'price' not in calls[0][1]['listing']
    assert review.request(conn, 'subito', 'abc', config=cfg)['status'] == 'completed'
    conn.execute('UPDATE listings SET last_price=35')
    assert review.read(conn, 'subito', 'abc', config=cfg)['stale']


def test_review_inference_pins_model_and_low(monkeypatch, tmp_path):
    from subitoo.ai import hermes_bridge
    from subitoo.ai.llm import LLMClient
    seen = []
    def complete(payload, cfg):
        seen.append((payload, cfg))
        return json.dumps({'choices': [{'message': {'content': json.dumps({'message': 'Modello versatile, ma ingombrante. Adatto a chi privilegia la flessibilità.'})}, 'finish_reason': 'stop'}]}).encode()
    monkeypatch.setattr(hermes_bridge, 'complete', complete)
    cfg = config(tmp_path, model='other-model', reasoning_effort='high')
    result = LLMClient(cfg).complete('review', {'listing': {'title': 'Acme Box 2'}})
    assert result['message'].startswith('Modello')
    payload, actual = seen[0]
    assert payload['model'] == actual.model == 'gpt-6.1-sol'
    assert payload['reasoning_effort'] == actual.reasoning_effort == 'low'
    assert 'massimo 60 parole' in payload['messages'][0]['content']
    assert cfg.model == 'other-model' and cfg.reasoning_effort == 'high'


@pytest.mark.parametrize('text', ['', '  ', 'x ' * 61, 'x' * 601, 'Vedi https://example.com'])
def test_review_rejects_empty_long_or_link_output(text):
    from subitoo.ai.review import Review
    with pytest.raises(ValueError):
        Review.model_validate({'message': text})


def test_review_persists_per_listing_release_year_and_reference_new_price(conn, tmp_path, monkeypatch):
    from subitoo.ai import review
    from subitoo.ai.worker import Worker
    cfg = config(tmp_path)
    monkeypatch.setattr(
        review,
        'fetch_web_snippets',
        lambda title, description=None: [
            {
                'id': 'web_1',
                'title': 'Trek Checkpoint ALR 5 2021 Specs',
                'url': 'https://www.trekbikes.com/it/it_IT/checkpoint-alr-5/',
                'snippet': 'Gruppo Shimano GRX RX810 2x11v, guarnitura Shimano GRX RX600 46/30T, listino 2199 EUR.',
            }
        ],
    )
    review.request(conn, 'subito', 'abc', config=cfg)
    seen_contexts = []
    class Model:
        def complete(self, task, context):
            seen_contexts.append(context)
            return {
                'message': 'Telaio in alluminio solido e versatile per sterrati, un po’ pesante in salita.',
                'product_name': 'Trek Checkpoint ALR 5',
                'release_year': 2021,
                'reference_price_eur': 2199,
                'reference_price_kind': 'launch_msrp',
                'drivetrain': 'Shimano GRX RX810 2x11v',
                'crankset': 'Shimano GRX RX600 46/30T',
                'lowest_gear_ratio': '30/34T · cassetta 11-34T',
            }
    assert Worker(conn, cfg, llm=Model()).run_once()
    assert seen_contexts[0]['web_snippets'][0]['title'] == 'Trek Checkpoint ALR 5 2021 Specs'
    state = review.read(conn, 'subito', 'abc', config=cfg)
    saved = state['result']
    assert saved['product_name'] == 'Trek Checkpoint ALR 5'
    assert saved['release_year'] == 2021
    assert saved['reference_price_eur'] == 2199.0
    assert saved['reference_price_kind'] == 'launch_msrp'
    assert saved['drivetrain'] == 'Shimano GRX RX810 2x11v'
    assert saved['crankset'] == 'Shimano GRX RX600 46/30T'
    assert saved['lowest_gear_ratio'] == '30/34T · cassetta 11-34T (0,88)'
    assert saved['source_ids'] == ['web_1']
    assert state['sources'][0]['url'] == 'https://www.trekbikes.com/it/it_IT/checkpoint-alr-5/'
    assert review.Review.model_validate({'message': 'Testo valido.', 'product_name': 'unknown', 'drivetrain': 'n/a', 'crankset': 'non specificato'}).product_name is None
    assert review.Review.model_validate({'message': 'Testo valido.', 'drivetrain': 'n/a'}).drivetrain is None
    assert review.Review.model_validate({'message': 'Testo valido.', 'crankset': 'non specificato'}).crankset is None
    assert review.Review.model_validate({'message': 'Testo valido.', 'lowest_gear_ratio': 'unknown'}).lowest_gear_ratio is None


def test_review_auto_fetches_and_caches_missing_vinted_description(conn, tmp_path, monkeypatch):
    from subitoo.ai import review
    from subitoo.ai.worker import Worker
    from subitoo.core import descriptions
    cfg = config(tmp_path)
    conn.execute("INSERT INTO queries(id,name,site,search_json,filters_json,cron,status_changed_at,created_at,updated_at) VALUES (9,'v','vinted','{}','{}','* * * * *',0,0,0)")
    conn.execute("INSERT INTO listings(query_id,site_listing_id,title,last_price,currency,url,raw_json,first_seen_at,last_seen_at) VALUES (9,'777','Bici gravel','900','EUR','https://www.vinted.it/items/777-bici-gravel','{}',1,1)")
    monkeypatch.setattr(descriptions, 'fetch_vinted_description', lambda url, item_id: 'Telaio carbonio taglia M gruppo GRX 810')
    review.request(conn, 'vinted', '777', config=cfg)
    seen_contexts = []
    class Model:
        def complete(self, task, context):
            seen_contexts.append(context)
            return {
                'message': 'Gravel leggera in carbonio con trasmissione affidabile.',
                'product_name': 'Canyon Grail CF SL 8',
                'release_year': 2022,
                'reference_price_eur': 2999,
                'reference_price_kind': 'launch_msrp',
            }
    assert Worker(conn, cfg, llm=Model()).run_once()
    assert seen_contexts[0]['listing']['description'] == 'Telaio carbonio taglia M gruppo GRX 810'
    state = review.read(conn, 'vinted', '777', config=cfg)
    assert state['status'] == 'completed' and state['stale'] is False
    cached = conn.execute("SELECT description FROM listing_descriptions WHERE site='vinted' AND site_listing_id='777'").fetchone()
    assert cached['description'] == 'Telaio carbonio taglia M gruppo GRX 810'
