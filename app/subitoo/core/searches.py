"""Build native marketplace searches from product text, without user-supplied URLs.

Prices are always enforced by the common core, even when also sent to the site.
Wallapop requires a reference point: central Italy, no radius or country guarantee.
It is not the user's location and does not require geolocation or an external API.
"""
from urllib.parse import urlencode
from pydantic import BaseModel, ConfigDict, Field
from subitoo.core.watches import Watch

SITES = ('subito', 'vinted', 'wallapop')


class QuickSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    cron: str = '*/30 * * * *'
    max_pages: int = Field(default=1, ge=1, le=3)


class QuickSearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    min_price_eur: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_price_eur: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    title_include: list[str] = Field(default_factory=list)
    title_exclude: list[str] = Field(default_factory=list)
    notify_on_price_change: bool = False
    sources: dict[str, QuickSource] = Field(default_factory=lambda: {s: QuickSource() for s in SITES})


def build_watch(data: dict) -> dict:
    search = QuickSearch.model_validate(data)
    text = ' '.join(search.query.split())
    if not text:
        raise ValueError('Scrivi cosa vuoi cercare.')
    if set(search.sources) - set(SITES):
        raise ValueError('Mercatino non supportato nella ricerca semplice.')
    if not any(source.enabled for source in search.sources.values()):
        raise ValueError('Seleziona almeno un mercatino.')
    vinted = {'search_text': text, 'order': 'newest_first'}
    wallapop = {'keywords': text, 'latitude': '42.5', 'longitude': '12.5', 'order_by': 'newest'}
    for bound, vinted_key, wallapop_key in ((search.min_price_eur, 'price_from', 'min_sale_price'),
                                          (search.max_price_eur, 'price_to', 'max_sale_price')):
        if bound is not None:
            vinted[vinted_key] = str(bound)
            wallapop[wallapop_key] = str(bound)
    urls = {
        'subito': 'https://www.subito.it/annunci-italia/vendita/usato/?' + urlencode({'q': text}),
        'vinted': 'https://www.vinted.it/catalog?' + urlencode(vinted),
        'wallapop': 'https://it.wallapop.com/app/search?' + urlencode(wallapop),
    }
    # Require all entered words in the title to reject fuzzy/padded site results.
    includes = list(dict.fromkeys(text.split() + search.title_include))
    spec = {**search.model_dump(exclude={'query', 'sources'}), 'name': text,
            'search_text': text, 'title_include': includes,
            'sources': {site: {**source.model_dump(), 'search_url': urls[site]}
                        for site, source in search.sources.items()}}
    return Watch.model_validate(spec).model_dump()
