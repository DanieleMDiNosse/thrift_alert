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


def parse_vinted_description(text: str, listing_id: str) -> str:
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
                    description = node['description'].strip()
                    if re.fullmatch(r'\$[0-9a-f]+', description):
                        raise RuntimeError('vinted: unresolved description reference')
                    return description
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    raise RuntimeError('vinted: no description bound to the requested item; page changed or access restricted')


def fetch_vinted_description(url: str, listing_id: str) -> str:
    if not can_load('vinted', listing_id, url):
        raise ValueError('URL articolo non valido per la descrizione.')
    ctx = build_context(get_settings(), needs_browser=False)
    response = ctx.http.get(url, headers={'RSC': '1'}, timeout=25, allow_redirects=False)
    if response.status_code in (301, 302, 303, 307, 308):
        target = urljoin(url, response.headers.get('location', ''))
        if (not response.headers.get('location') or not can_load('vinted', listing_id, target)
                or urlsplit(target).netloc != urlsplit(url).netloc):
            raise RuntimeError('vinted: redirected outside the requested item; no follow attempted')
        response = ctx.http.get(target, headers={'RSC': '1'}, timeout=25, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f'vinted: description request HTTP {response.status_code}')
    if response.headers.get('content-type', '').startswith('text/html'):
        raise RuntimeError('vinted: description page is not a flight payload')
    if len(response.content) > 4 * 1024 * 1024:
        raise RuntimeError('vinted: description response exceeds size limit')
    return parse_vinted_description(response.text, listing_id)


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
    description = fetch_vinted_description(row['url'], listing_id)
    from subitoo.core.db import now
    conn.execute('INSERT OR REPLACE INTO listing_descriptions VALUES (?,?,?,?)',
                 (site, listing_id, description, now()))
    return description
