"""Explicit opt-in live smoke: no pushes or DB writes; one page per marketplace.

MARKETWATCH_LIVE=1 app/.venv/bin/python -m pytest -m live -q -s app/tests
This proves current response compatibility, not platform permission or completeness.
"""
import json
import os
from pathlib import Path
import pytest

from subitoo.config import get_settings
from subitoo.core.fetch import build_context
from subitoo.registry import get_site


@pytest.mark.live
@pytest.mark.skipif(os.environ.get('MARKETWATCH_LIVE') != '1', reason='live network explicitly opt-in')
@pytest.mark.parametrize('site', ['subito', 'vinted', 'wallapop'])
def test_live_one_page(site):
    path = Path(__file__).resolve().parents[2] / 'examples' / 'connectivity-demo.json'
    source = json.loads(path.read_text())['sources'][site]
    adapter = get_site(site)
    search = adapter.validate_search({'url': source['search_url'], 'max_pages': '1'})
    listings = list(adapter.fetch(search, build_context(get_settings(), needs_browser=adapter.needs_browser)))
    assert listings, 'known broad smoke search returned no listings; inspect rather than claim support'
    assert all(x.site_listing_id and x.title and x.url.startswith('https://') for x in listings)
    print(f'{site}: {len(listings)} normalized live listings; no writes/pushes')
