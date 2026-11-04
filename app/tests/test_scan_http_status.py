"""Transport status is preserved; forbidden responses are not retried in-place."""
from types import SimpleNamespace

import pytest

from subitoo.core.recovery import HTTPFailure, retry_after_seconds
from subitoo.sites.vinted import VintedSite


@pytest.mark.parametrize('status', [401, 403, 429, 503])
def test_vinted_catalog_failure_has_status_no_rewarm_and_no_body(status):
    calls = []
    response = SimpleNamespace(status_code=status, headers={'Retry-After': '7200'},
                               text='private server body must not be logged')
    def get(*args, **kwargs):
        calls.append(args[0])
        return response
    ctx = SimpleNamespace(http=SimpleNamespace(get=get))
    with pytest.raises(HTTPFailure) as caught:
        VintedSite()._get_page(ctx, 'https://www.vinted.it/', 'https://www.vinted.it/catalog?search_text=sony')
    assert caught.value.status == status and caught.value.retry_after == 7200
    assert len(calls) == 1
    assert 'private server body' not in str(caught.value)
    assert 'search_text' not in str(caught.value)


def test_vinted_initial_warm_rate_limit_stops_before_catalog():
    calls = []
    def get(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(status_code=429, headers={'Retry-After': '3600'}, text='')
    ctx = SimpleNamespace(http=SimpleNamespace(get=get))
    with pytest.raises(HTTPFailure) as caught:
        VintedSite().fetch({'url': 'https://www.vinted.it/catalog?search_text=sony'}, ctx)
    assert caught.value.status == 429 and len(calls) == 1


def test_retry_after_handles_delta_http_date_and_invalid_values():
    assert retry_after_seconds('7200', now_ts=0) == 7200
    assert retry_after_seconds('Thu, 01 Jan 1970 02:00:00 GMT', now_ts=0) == 7200
    assert retry_after_seconds('bad', now_ts=0) == 0
    assert retry_after_seconds('-1', now_ts=0) == 0
