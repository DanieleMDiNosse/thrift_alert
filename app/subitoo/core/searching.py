"""Multi-phrase searches with a shared native-page budget and explicit coverage.

A completed scan means the exposed native pagination ended, not that the platform
returned every real advertisement. Search ranking/visibility/site caps still apply.
"""
from dataclasses import dataclass
import json
import random
import time

from subitoo.core.filters import apply_filters

PAGE_BUDGETS = {'subito': 30, 'vinted': 10, 'wallapop': 25}


@dataclass
class QueryScan:
    listings: list
    coverage: dict
    expanded_baseline: bool = False


def fetch_query(conn, q, adapter, ctx, *, sleep=time.sleep) -> QueryScan:
    owner = conn.execute('SELECT w.spec_json FROM watches w JOIN watch_sources s '
                         'ON s.watch_id=w.id WHERE s.query_id=?', (q.id,)).fetchone()
    quick = bool(owner and json.loads(owner['spec_json']).get('search_text'))
    urls = q.search.get('urls') or [q.search.get('url')]
    if not isinstance(urls, list) or not 1 <= len(urls) <= 50:
        raise ValueError('search must contain between one and fifty native URLs')
    budget = PAGE_BUDGETS.get(q.site) if quick else None
    pages_per_phrase = max(1, budget // len(urls)) if budget else q.search.get('max_pages', '1')
    previous = conn.execute('SELECT 1 FROM scan_coverage WHERE query_id=?', (q.id,)).fetchone()
    stop_page = None
    if quick and q.seeded and previous is not None and not q.filters.description_include and ctx is not None:
        rows = conn.execute(
            'SELECT site_listing_id, last_price FROM listings WHERE query_id=?', (q.id,)
        ).fetchall()
        if rows:
            known_prices = {row['site_listing_id']: row['last_price'] for row in rows}
            basic = q.filters.model_copy(update={'description_include': None})
            has_price_bound = q.filters.price_min is not None or q.filters.price_max is not None

            def stop_page(page_items: list) -> bool:
                matched = apply_filters(page_items, basic)
                if has_price_bound:
                    matched = [x for x in matched if x.currency == 'EUR']
                return bool(matched) and all(
                    x.site_listing_id in known_prices
                    and (not q.notify_on_price_change or x.price == known_prices[x.site_listing_id])
                    for x in matched
                )

    all_items, scans = {}, []
    if ctx is not None:
        try:
            ctx._warmed_roots = set()
            if stop_page is not None:
                ctx.stop_page = stop_page
        except AttributeError:
            pass
    try:
        for i, url in enumerate(urls):
            search = dict(q.search)
            search.pop('urls', None)
            if url is not None:
                search['url'] = url
            if i:
                # Never replay a resolved API URL belonging to a different phrase.
                search.pop('api_url', None)
                gap = (1.2, 2.5) if len(urls) > 5 else (2.5, 5.0)
                sleep(random.uniform(*gap))
            if quick:
                search['max_pages'] = str(pages_per_phrase)
                search['price_min'], search['price_max'] = q.filters.price_min, q.filters.price_max
            fetched = adapter.fetch(search, ctx)
            observed = getattr(fetched, 'coverage', None)
            scans.append(dict(observed) if observed is not None else
                         {'pages': None, 'complete': False, 'reason': 'unreported'})
            for item in fetched:
                all_items[item.site_listing_id] = item
    finally:
        if ctx is not None:
            for attr in ('stop_page', '_warmed_roots', '_subito_api_url'):
                if hasattr(ctx, attr):
                    try:
                        setattr(ctx, attr, None)
                    except AttributeError:
                        pass
    complete = all(scan.get('complete') is True for scan in scans)
    reasons = list(dict.fromkeys(scan.get('reason', 'unreported') for scan in scans if not scan.get('complete')))
    coverage = {'complete': complete, 'reason': 'end' if complete else ', '.join(reasons),
                'pages': sum(scan['pages'] for scan in scans if isinstance(scan.get('pages'), int)),
                'phrases': len(urls), 'page_budget': budget, 'searches': scans}
    # Upgrade old quick searches silently: a wider backlog is not newly published.
    return QueryScan(list(all_items.values()), coverage, expanded_baseline=quick and previous is None)
