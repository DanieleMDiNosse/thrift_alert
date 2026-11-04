"""Optional description text: reuse search payloads, load Vinted details on demand.

No detail-page fan-out during polling or GUI refresh. Cache only successful observed
text (including an explicitly empty description), not transport/schema failures.
"""
import json
import re
from urllib.parse import urlsplit, urljoin

from subitoo.config import get_settings
from subitoo.core.fetch import build_context
from subitoo.registry import get_site

MAX_ITEM_REDIRECTS = 3


class DescriptionUnavailable(RuntimeError):
    """This single item's description cannot be read; other items are unaffected.

    kind: 'gone' (removed, permanent), 'redirect' or 'parse' (retried a few times).
    Site-wide problems (429, 403, 5xx, network, challenge pages) are NOT this type.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def native_description(site: str, raw_json: str) -> str | None:
    try:
        raw = json.loads(raw_json)
        value = raw.get('body' if site == 'subito' else 'description')
    except (ValueError, TypeError, AttributeError):
        return None
    return value.strip() if isinstance(value, str) else None


def can_load(site: str, listing_id: str, url: str | None) -> bool:
    if site != 'vinted' or not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        return bool(parts.scheme == 'https' and not parts.username and not parts.password
                    and parts.port in (None, 443) and get_site('vinted').claims_url(url)
                    and re.fullmatch(r'\d+', listing_id)
                    and re.match(r'/items/' + re.escape(listing_id) + r'(?:-|/|$)', parts.path))
    except ValueError:
        return False


def _flight_text_chunk(raw: bytes, ref: str) -> str | None:
    """Resolve a React flight `$<hex>` reference to its `<hex>:T<len>,<text>` row.

    Long strings are streamed as separate text rows whose length is a hex count of
    UTF-8 bytes, so slicing must happen on bytes, not on decoded characters.
    """
    match = re.search(rb'(?:^|\n)' + re.escape(ref.encode()) + rb':T([0-9a-f]+),', raw)
    if match is None:
        return None
    start = match.end()
    chunk = raw[start:start + int(match.group(1), 16)]
    try:
        return chunk.decode('utf-8')
    except UnicodeDecodeError:
        return None


def parse_vinted_description(text: str, listing_id: str, raw: bytes | None = None) -> str:
    decoder = json.JSONDecoder()
    for line in text.splitlines():
        value = line.partition(':')[2]
        if not value.startswith(('{', '[')):
            continue
        try:
            root, _ = decoder.raw_decode(value)
        except ValueError:
            continue
        stack = [root]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                # The description plugin carries the exact item ID. Do not confuse
                # recommended products or an unrelated SEO description with this item.
                if str(node.get('item_id')) == listing_id and isinstance(node.get('description'), str):
                    description = node['description']
                    reference = re.fullmatch(r'\$([0-9a-f]+)', description)
                    if reference:
                        resolved = _flight_text_chunk(
                            raw if raw is not None else text.encode('utf-8'), reference.group(1))
                        if resolved is None:
                            raise RuntimeError('vinted: unresolved description reference')
                        description = resolved
                    elif description.startswith('$$'):
                        description = description[1:]  # flight escape for a literal '$'
                    return description.strip()
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    raise RuntimeError('vinted: no description bound to the requested item; page changed or access restricted')


def _rsc_url(url: str) -> str:
    """Append `?_rsc` when the item URL has no query string to skip Vinted's 307 hop."""
    return url if urlsplit(url).query else f'{url}?_rsc'


def fetch_vinted_description(url: str, listing_id: str, *, ctx=None) -> str:
    if not can_load('vinted', listing_id, url):
        raise ValueError('URL articolo non valido per la descrizione.')
    if ctx is None:
        ctx = build_context(get_settings(), needs_browser=False)
    current = _rsc_url(url)
    response = ctx.http.get(current, headers={'RSC': '1'}, timeout=25, allow_redirects=False)
    # Vinted may chain a canonical-slug redirect (edited title) and an `?_rsc` redirect.
    # Follow a few hops, each re-validated as the same item on the same origin.
    for _ in range(MAX_ITEM_REDIRECTS):
        if response.status_code not in (301, 302, 303, 307, 308):
            break
        target = urljoin(current, response.headers.get('location', ''))
        if (not response.headers.get('location') or not can_load('vinted', listing_id, target)
                or urlsplit(target).netloc != urlsplit(url).netloc):
            raise DescriptionUnavailable(
                'redirect', 'vinted: redirected outside the requested item; no follow attempted')
        current = _rsc_url(target)
        response = ctx.http.get(current, headers={'RSC': '1'}, timeout=25, allow_redirects=False)
    if response.status_code in (301, 302, 303, 307, 308):
        raise DescriptionUnavailable('redirect', 'vinted: too many item redirects')
    if response.status_code in (404, 410):
        raise DescriptionUnavailable('gone', f'vinted: item no longer available (HTTP {response.status_code})')
    if response.status_code != 200:
        from subitoo.core.recovery import HTTPFailure, retry_after_seconds
        raise HTTPFailure('vinted', response.status_code,
                          retry_after=retry_after_seconds(response.headers.get('Retry-After')))
    if response.headers.get('content-type', '').startswith('text/html'):
        raise RuntimeError('vinted: description page is not a flight payload')
    if len(response.content) > 4 * 1024 * 1024:
        raise DescriptionUnavailable('parse', 'vinted: description response exceeds size limit')
    try:
        return parse_vinted_description(response.text, listing_id, response.content)
    except RuntimeError as error:
        raise DescriptionUnavailable('parse', str(error)) from error


def load_description(conn, site: str, listing_id: str) -> str:
    row = conn.execute('''SELECT l.url,l.raw_json FROM listings l JOIN queries q ON q.id=l.query_id
                          WHERE q.site=? AND l.site_listing_id=? ORDER BY l.last_seen_at DESC LIMIT 1''',
                       (site, listing_id)).fetchone()
    if row is None:
        raise LookupError('Annuncio non trovato.')
    cached = conn.execute('SELECT description FROM listing_descriptions WHERE site=? AND site_listing_id=?',
                          (site, listing_id)).fetchone()
    if cached is not None:
        return cached['description']
    native = native_description(site, row['raw_json'])
    if native is not None:
        return native
    if not can_load(site, listing_id, row['url']):
        raise ValueError('Descrizione non disponibile: apri l’annuncio originale.')
    from subitoo.core import db, recovery
    if recovery.site_retry_at(conn, site) > db.now():
        raise ValueError('Mercatino in pausa per limite richieste: attendi prima di caricare altre descrizioni.')
    try:
        description = fetch_vinted_description(row['url'], listing_id)
    except recovery.HTTPFailure as error:
        if error.status == 429:
            recovery.limit_site(conn, site, error, now_ts=db.now())
        raise
    from subitoo.core.db import now
    conn.execute('INSERT OR REPLACE INTO listing_descriptions VALUES (?,?,?,?)',
                 (site, listing_id, description, now()))
    return description
