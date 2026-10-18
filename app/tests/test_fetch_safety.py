import pytest
from subitoo.sites.subito import parse_items
from subitoo.sites.vinted import parse_items as vinted_items
from subitoo.registry import get_site


@pytest.mark.parametrize('payload', [{}, {'ads': None}, {'ads': {}}, {'ads': [None]}, {'ads': [{'id': '', 'subject': 'bad'}]}])
def test_subito_schema_errors_are_not_empty_success(payload):
    with pytest.raises(RuntimeError):
        parse_items(payload)


def test_subito_explicit_empty_is_valid():
    assert parse_items({'ads': []}) == []


@pytest.mark.parametrize('payload', [{'items': [None]}, {'items': [{}]}, {'items': [{'productItem': {}}]}])
def test_vinted_malformed_items_fail(payload):
    with pytest.raises(RuntimeError):
        vinted_items(payload)


@pytest.mark.parametrize('url', ['ftp://www.subito.it/search', 'https://name:secret@www.subito.it/search', 'https://www.subito.it:1234/search', 'https://www.subito.it.evil.example/search'])
def test_invalid_url_or_embedded_credentials_rejected(url):
    with pytest.raises(ValueError):
        get_site('subito').validate_search({'url': url})
