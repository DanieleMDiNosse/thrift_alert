"""Build native marketplace searches from product text, without user-supplied URLs.

Prices are always enforced by the common core, even when also sent to the site.
Wallapop requires a reference point: central Italy, no radius or country guarantee.
It is not the user's location and does not require geolocation or an external API.
"""
from urllib.parse import urlencode
from pydantic import BaseModel, ConfigDict, Field
from subitoo.core.watches import Watch

SITES = ('subito', 'vinted', 'wallapop')
MAX_PHRASES = 50
MAX_QUERY_CHARS = 6050


def _compress_phrases_for_urls(phrases: list[str]) -> list[str]:
    """When many phrases (>5) are configured, collapse groups sharing a common leading
    word (>=3 chars) into their longest common whole-word prefix for marketplace URL
    queries. Exact phrase matching remains enforced locally via Watch.search_phrases."""
    if len(phrases) <= 5:
        return phrases
    groups: dict[str, list[list[str]]] = {}
    order: list[tuple[str, str]] = []
    for phrase in phrases:
        words = phrase.split()
        head = words[0].casefold() if words else ''
        if len(head) >= 3:
            if head not in groups:
                groups[head] = []
                order.append(('group', head))
            groups[head].append(words)
        else:
            order.append(('single', phrase))
    compressed: list[str] = []
    seen: set[str] = set()
    for kind, value in order:
        if kind == 'single':
            candidates = [value]
        else:
            members = groups[value]
            if len(members) == 1:
                candidates = [' '.join(members[0])]
            else:
                prefix = list(members[0])
                for other in members[1:]:
                    i = 0
                    while i < len(prefix) and i < len(other) and prefix[i].casefold() == other[i].casefold():
                        i += 1
                    prefix = prefix[:max(1, i)]
                candidates = [' '.join(prefix)]
        for c in candidates:
            key = c.casefold()
            if key not in seen:
                seen.add(key)
                compressed.append(c)
    return compressed


class QuickSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    cron: str = '*/30 * * * *'
    max_pages: int = Field(default=1, ge=1, le=30)


class QuickSearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, max_length=120)
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    enabled: bool = True
    min_price_eur: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_price_eur: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    title_include: list[str] = Field(default_factory=list)
    title_exclude: list[str] = Field(default_factory=list)
    description_include: list[str] = Field(default_factory=list)
    notify_on_price_change: bool = False
    sources: dict[str, QuickSource] = Field(default_factory=lambda: {s: QuickSource() for s in SITES})


def build_watch(data: dict) -> dict:
    search = QuickSearch.model_validate(data)
    phrases = []
    seen = set()
    for line in search.query.splitlines():
        phrase = ' '.join(line.split())
        if not phrase:
            continue
        if len(phrase) > 120:
            raise ValueError('Ogni frase può avere al massimo 120 caratteri.')
        if phrase.casefold() not in seen:
            phrases.append(phrase)
            seen.add(phrase.casefold())
    if not 1 <= len(phrases) <= MAX_PHRASES:
        raise ValueError(f'Inserisci da una a {MAX_PHRASES} frasi, una per riga.')
    if set(search.sources) - set(SITES):
        raise ValueError('Mercatino non supportato nella ricerca semplice.')
    if not any(source.enabled for source in search.sources.values()):
        raise ValueError('Seleziona almeno un mercatino.')
    url_phrases = _compress_phrases_for_urls(phrases)
    urls = {site: [] for site in SITES}
    for phrase in url_phrases:
        vinted = {'search_text': phrase, 'order': 'newest_first'}
        wallapop = {'keywords': phrase, 'latitude': '42.5', 'longitude': '12.5', 'order_by': 'newest'}
        for bound, vinted_key, wallapop_key in ((search.min_price_eur, 'price_from', 'min_sale_price'),
                                              (search.max_price_eur, 'price_to', 'max_sale_price')):
            if bound is not None:
                vinted[vinted_key] = str(bound)
                wallapop[wallapop_key] = str(bound)
        urls['subito'].append('https://www.subito.it/annunci-italia/vendita/usato/?' + urlencode({'q': phrase}))
        urls['vinted'].append('https://www.vinted.it/catalog?' + urlencode(vinted))
        urls['wallapop'].append('https://it.wallapop.com/app/search?' + urlencode(wallapop))
    includes = list(dict.fromkeys((phrases[0].split() if len(phrases) == 1 else []) + search.title_include))
    custom_name = (search.name or '').strip()
    spec = {**search.model_dump(exclude={'query', 'sources', 'name'}), 'name': custom_name or phrases[0],
            'search_text': '\n'.join(phrases), 'search_phrases': phrases, 'title_include': includes,
            'sources': {site: {**source.model_dump(), 'search_url': urls[site][0],
                               'search_urls': urls[site]}
                        for site, source in search.sources.items()}}
    return Watch.model_validate(spec).model_dump()
