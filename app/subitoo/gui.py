"""Single-user loopback GUI. No remote dashboard, new scheduler or web framework.

Each request opens its own SQLite connection and reuses the existing watch core.
Local browser writes require a per-process CSRF token plus Host/Origin validation.
"""
from __future__ import annotations

from contextlib import closing
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit, parse_qs

from pydantic import ValidationError

from subitoo.config import get_settings
from subitoo.core import db, watches, descriptions, recovery
from subitoo.core.models import Notification

ASSETS = Path(__file__).with_name('web')
MAX_BODY = 65536


def _safe_link(value):
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme in ('https', 'http') and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return None


def _safe_image(value):
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname or ''
        allowed = any(host.endswith('.' + suffix) for suffix in
                      ('vinted.net', 'sbito.it', 'wallapop.com', 'autoscout24.net'))
        if (allowed and parts.scheme == 'https' and parts.port in (None, 443)
                and not parts.username and not parts.password):
            return value
    except ValueError:
        pass
    return None


def _lock_held(path: Path) -> bool:
    try:
        with path.open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def _monitor_running():
    return _lock_held(Path(get_settings().db_path + '.dispatcher.lock'))


def _ai_worker_running():
    resolved = Path(get_settings().db_path).expanduser().resolve()
    return _lock_held(Path(str(resolved) + '.ai-worker.lock'))


def _percentile(sorted_vals: list[float], q: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _price_distribution(prices: list[float]) -> dict | None:
    if not prices:
        return None
    n = len(prices)
    lo, hi = prices[0], prices[-1]
    median = round(_percentile(prices, 0.5), 2)
    p25 = round(_percentile(prices, 0.25), 2)
    p75 = round(_percentile(prices, 0.75), 2)
    mean = round(sum(prices) / n, 2)
    if hi <= lo:
        bins = [{'min': round(lo, 2), 'max': round(hi, 2), 'count': n}]
    else:
        n_bins = min(8, max(4, int(n ** 0.5) + 2)) if n >= 4 else n
        step = (hi - lo) / n_bins
        bins = []
        for i in range(n_bins):
            b_lo = lo + i * step
            b_hi = hi if i == n_bins - 1 else lo + (i + 1) * step
            bins.append({'min': round(b_lo, 2), 'max': round(b_hi, 2), 'count': 0})
        for p in prices:
            idx = min(n_bins - 1, max(0, int((p - lo) / step)))
            bins[idx]['count'] += 1
    return {
        'count': n,
        'currency': 'EUR',
        'min': round(lo, 2),
        'p25': p25,
        'median': median,
        'mean': mean,
        'p75': p75,
        'max': round(hi, 2),
        'bins': bins,
    }


def _state(conn, listing_site=None, listing_offset=0, watch_id=None,
           listing_order='balanced_recency', listing_q=None, listing_view='all'):
    orders = {
        'balanced_recency': 'site_position,site',
        'price_asc': 'price IS NULL,currency,price ASC,last_seen_at DESC,site,site_listing_id',
        'price_desc': 'price IS NULL,currency,price DESC,last_seen_at DESC,site,site_listing_id',
    }
    if listing_order not in orders:
        raise ValueError('Invalid listing order')
    if listing_view not in ('all', 'starred', 'hidden'):
        raise ValueError('Invalid listing view')
    q_pattern = None
    if listing_q is not None:
        cleaned = listing_q.strip()
        if len(cleaned) > 120 or any(ord(c) < 32 for c in cleaned):
            raise ValueError('Invalid listing search')
        if cleaned:
            escaped = re.sub(r'([%_\\])', r'\\\1', cleaned.lower())
            q_pattern = f'%{escaped}%'
        else:
            listing_q = None
    groups = watches.list_all(conn)
    for group in groups:
        group['queries'] = []
        for query in watches.queries(conn, group['id']):
            coverage = conn.execute('SELECT coverage_json,scanned_at,description_skipped FROM scan_coverage WHERE query_id=?', (query.id,)).fetchone()
            attempt = conn.execute('SELECT status,started_at,finished_at,error_message FROM runs WHERE query_id=? ORDER BY id DESC LIMIT 1',(query.id,)).fetchone()
            last_attempt = None
            if attempt:
                code = None
                if attempt['status'] == 'error':
                    code = recovery.classify(attempt['error_message'] or '').code
                last_attempt = {'status':attempt['status'],'started_at':attempt['started_at'],
                                'finished_at':attempt['finished_at'],'error_code':code}
            group['queries'].append({
                'recovery': recovery.snapshot(conn, query.id),
                'site_retry_at': recovery.site_retry_at(conn, query.site),
                'description_pending': (conn.execute('SELECT pending_count FROM description_scans WHERE query_id=?',
                                                     (query.id,)).fetchone() or [None])[0],
                'coverage': json.loads(coverage[0]) if coverage else None,
                'coverage_observed_at': coverage['scanned_at'] if coverage else None,
                'description_skipped': coverage['description_skipped'] if coverage else 0,
                'last_attempt': last_attempt,
                'id': query.id, 'site': query.site, 'enabled': query.enabled,
                'seeded': query.seeded, 'status': query.status.value,
                'last_run_at': query.last_run_at,
                # Do not forward exception text from legacy/raw historical rows.
                'error': 'Scansione sospesa dopo un errore. Controlla accesso e URL prima di riprovare.'
                         if query.status.value == 'error' else None,
            })
    observed_base = '''
        WITH observed AS (
            SELECT l.id,l.title,l.last_price AS price,l.currency,l.url,l.image_url,q.site,l.site_listing_id,l.raw_json,
                   d.description AS cached_description,
                   COALESCE(f.starred, 0) AS starred,
                   COALESCE(f.hidden, 0) AS hidden,
                   COALESCE(w.name,q.name) AS watch_name,l.first_seen_at,l.last_seen_at,
                   ROW_NUMBER() OVER (PARTITION BY q.site,l.site_listing_id
                                      ORDER BY l.last_seen_at DESC,l.id DESC) AS position
            FROM listings l JOIN queries q ON q.id=l.query_id
            LEFT JOIN watch_sources s ON s.query_id=q.id
            LEFT JOIN watches w ON w.id=s.watch_id
            LEFT JOIN listing_descriptions d ON d.site=q.site AND d.site_listing_id=l.site_listing_id
            LEFT JOIN listing_flags f ON f.site=q.site AND f.site_listing_id=l.site_listing_id
            WHERE (? IS NULL OR s.watch_id=?)
        ), deduped AS (SELECT * FROM observed WHERE position=1)
    '''
    flag_totals = conn.execute(
        observed_base + ' SELECT COALESCE(SUM(CASE WHEN starred=1 AND hidden=0 THEN 1 ELSE 0 END),0) AS starred_n,'
                        ' COALESCE(SUM(CASE WHEN hidden=1 THEN 1 ELSE 0 END),0) AS hidden_n FROM deduped',
        (watch_id, watch_id),
    ).fetchone()
    observed = observed_base + ''',
        unique_ads AS (
            SELECT * FROM deduped
            WHERE ((? = 'all' AND hidden = 0) OR (? = 'starred' AND starred = 1 AND hidden = 0) OR (? = 'hidden' AND hidden = 1))
              AND (? IS NULL OR LOWER(title) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(cached_description, '')) LIKE ? ESCAPE '\\')
        )
    '''
    filter_params = (watch_id, watch_id, listing_view, listing_view, listing_view, q_pattern, q_pattern, q_pattern)
    counts = {row['site']: row['n'] for row in conn.execute(
        observed + ' SELECT site,COUNT(*) AS n FROM unique_ads GROUP BY site', filter_params)}
    for site in ('subito', 'vinted', 'wallapop'):
        counts.setdefault(site, 0)
    total = counts.get(listing_site, 0) if listing_site else sum(counts.values())
    price_dist = None
    if watch_id is not None:
        price_rows = conn.execute(
            observed
            + " SELECT price FROM unique_ads WHERE (? IS NULL OR site=?)"
              " AND price IS NOT NULL AND price > 0 AND COALESCE(currency, 'EUR') = 'EUR'"
              " ORDER BY price ASC",
            (*filter_params, listing_site, listing_site),
        ).fetchall()
        price_dist = _price_distribution([float(r['price']) for r in price_rows])
    page_size = 40
    listing_offset = min(listing_offset, max(0, ((total - 1) // page_size) * page_size))
    # Balance the overview explicitly, not by accidental row order at equal scan
    # timestamps. Within each site, most recently observed listings appear first.
    rows = conn.execute(observed + '''
        , ranked AS (
            SELECT *,ROW_NUMBER() OVER(PARTITION BY site ORDER BY last_seen_at DESC,id DESC) AS site_position
            FROM unique_ads WHERE (? IS NULL OR site=?)
        ) SELECT title,price,currency,url,image_url,site,site_listing_id,raw_json,cached_description,
                 starred,hidden,watch_name,first_seen_at,last_seen_at
          FROM ranked ORDER BY ''' + orders[listing_order] + ''' LIMIT ? OFFSET ?
    ''', (*filter_params, listing_site, listing_site, page_size, listing_offset)).fetchall()
    listings = []
    for row in rows:
        item = dict(row)
        raw = item.pop('raw_json')
        cached = item.pop('cached_description')
        item['starred'] = bool(item.pop('starred', 0))
        item['hidden'] = bool(item.pop('hidden', 0))
        item['url'] = _safe_link(item['url'])
        item['image_url'] = _safe_image(item['image_url'])
        item['description'] = cached if cached is not None else descriptions.native_description(item['site'], raw)
        item['can_load_description'] = item['description'] is None and descriptions.can_load(
            item['site'], item['site_listing_id'], item['url'])
        listings.append(item)
    try:
        from subitoo.core.notify import active_notifier
        active_notifier().validate_config()
        configured = True
    except Exception:
        configured = False
    return {'watches': groups, 'listings': listings, 'monitor_running': _monitor_running(),
            'ai_worker_running': _ai_worker_running(),
            'listing_page': {'total': total, 'offset': listing_offset, 'limit': page_size,
                             'has_more': listing_offset + len(listings) < total,
                             'site_counts': counts, 'order': listing_order, 'watch_id': watch_id,
                             'view': listing_view, 'q': listing_q or '',
                             'starred_count': int(flag_totals['starred_n']),
                             'hidden_count': int(flag_totals['hidden_n']),
                             'price_distribution': price_dist},
            'notification_configured': configured, 'timezone': get_settings().tz}


class GUIHandler(BaseHTTPRequestHandler):
    # Logs must not capture private search URLs, input values or CSRF tokens.
    def log_message(self, format, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _reply(self, status, value, content_type='application/json; charset=utf-8'):
        body = (json.dumps(value, ensure_ascii=False).encode('utf-8')
                if isinstance(value, dict) else value.encode('utf-8'))
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' https://*.vinted.net https://*.sbito.it https://*.wallapop.com https://*.autoscout24.net; "
                         "connect-src 'self'; object-src 'none'; "
                         "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in allowed:
            self._reply(403, {'error': 'Accesso consentito soltanto dal browser locale.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in allowed}:
            self._reply(403, {'error': 'Origine della richiesta non consentita.'})
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self._reply(403, {'error': 'Richiesta da un altro sito non consentita.'})
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        if urlsplit(self.path).path in {'/api/ai/worth', '/api/ai/review'}:
            try:
                params = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=4)
                if set(params) != {'site', 'id'} or any(len(value) != 1 for value in params.values()):
                    raise ValueError('Invalid listing identity')
                if urlsplit(self.path).path == '/api/ai/review':
                    from subitoo.ai.review import read
                else:
                    from subitoo.ai.worth import read
                with closing(db.init_db()) as conn:
                    conn.execute('BEGIN')
                    result = read(conn, params['site'][0], params['id'][0])
                    conn.commit()
                self._reply(200, result)
            except ValueError:
                self._reply(400, {'error': 'Annuncio non disponibile o identità non valida.'})
            except Exception:
                self._reply(503, {'error': 'Giudizio non disponibile. Il monitor resta indipendente.'})
            return
        if urlsplit(self.path).path == '/api/ai/state':
            try:
                from subitoo.ai.service import snapshot
                params = parse_qs(urlsplit(self.path).query, max_num_fields=4)
                if any(len(values) != 1 for values in params.values()):
                    raise ValueError('Duplicate query parameters')
                category = params.get('category', [None])[0]
                watch = params.get('watch_id', [None])[0]
                if category is not None and not re.fullmatch(r'[a-z_]{1,40}', category):
                    raise ValueError('Invalid category')
                if watch is not None and not re.fullmatch(r'[1-9][0-9]{0,9}', watch):
                    raise ValueError('Invalid watch')
                with closing(db.init_db()) as conn:
                    conn.execute('BEGIN')
                    result = snapshot(conn, category=category, watch_id=int(watch) if watch else None)
                    conn.commit()
                self._reply(200, result)
            except ValueError:
                self._reply(400, {'error': 'Filtro AI non valido.'})
            except Exception:
                self._reply(503, {'error': 'Ricerca AI non disponibile. Il monitor resta indipendente.'})
            return
        if urlsplit(self.path).path == '/api/state':
            try:
                params = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=6)
                allowed_params = {'listing_site', 'listing_offset', 'watch_id', 'listing_order', 'listing_q', 'listing_view'}
                if set(params) - allowed_params or any(len(v) != 1 for v in params.values()):
                    raise ValueError('Invalid listing filters')
                site = params.get('listing_site', [''])[0] or None
                offset = params.get('listing_offset', ['0'])[0]
                order = params.get('listing_order', ['balanced_recency'])[0]
                watch = params.get('watch_id', [None])[0]
                listing_q = params.get('listing_q', [None])[0]
                listing_view = params.get('listing_view', ['all'])[0] or 'all'
                if watch is not None and not re.fullmatch(r'[1-9][0-9]{0,9}', watch):
                    raise ValueError('Invalid watch')
                if site not in (None, 'subito', 'vinted', 'wallapop') or not re.fullmatch(r'[0-9]{1,9}', offset):
                    raise ValueError('Invalid listing filters')
                with closing(db.init_db()) as conn:
                    # One WAL snapshot across groups, sources and listings. A parallel
                    # delete/edit must not invalidate subsequent reads in this response.
                    conn.execute('BEGIN')
                    state = _state(
                        conn,
                        listing_site=site,
                        listing_offset=int(offset),
                        watch_id=int(watch) if watch else None,
                        listing_order=order,
                        listing_q=listing_q,
                        listing_view=listing_view,
                    )
                    conn.commit()
                self._reply(200, state)
            except ValueError:
                self._reply(400, {'error': 'Filtro annunci non valido.'})
            except sqlite3.Error:
                self._reply(503, {'error': 'Database occupato o non disponibile. Riprova tra poco.'})
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/static/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/static/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/static/ai.js': ('ai.js', 'text/javascript; charset=utf-8'),
                  '/static/ai.css': ('ai.css', 'text/css; charset=utf-8'),
                  '/static/worth.js': ('worth.js', 'text/javascript; charset=utf-8'),
                  '/static/worth.css': ('worth.css', 'text/css; charset=utf-8')}
        if self.path not in assets:
            self._reply(404, {'error': 'Pagina non trovata.'})
            return
        filename, mime = assets[self.path]
        text = (ASSETS / filename).read_text(encoding='utf-8')
        if filename == 'index.html':
            text = text.replace('__CSRF_TOKEN__', self.server.csrf_token)
            from subitoo.ai.config import get_config
            if get_config().workflow == 'on_demand':
                text = text.replace('<section id="ai-assistant"', '<section id="ai-assistant" data-workflow="on_demand" hidden')
        self._reply(200, text, mime)

    def do_POST(self):
        if not self._guard():
            return
        token = self.headers.get('X-CSRF-Token', '')
        if not secrets.compare_digest(token.encode(), self.server.csrf_token.encode()):
            self._reply(403, {'error': 'Sessione non valida. Ricarica la pagina e riprova.'})
            return
        if self.headers.get_content_type() != 'application/json':
            self._reply(415, {'error': 'È richiesto un corpo JSON.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self._reply(413 if length > MAX_BODY else 400, {'error': 'Dimensione della richiesta non valida.'})
            return
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._reply(400, {'error': 'La richiesta non è un oggetto JSON valido.'})
            return
        try:
            with closing(db.init_db()) as conn:
                result = self._action(conn, data)
            self._reply(200, result)
        except LookupError:
            self._reply(404, {'error': 'Ricerca non trovata. Aggiorna la pagina.'})
        except ValidationError as error:
            errors = error.errors(include_input=False, include_url=False)
            detail = '; '.join(f'{" / ".join(map(str, e["loc"]))}: {e["msg"]}' for e in errors[:3])
            self._reply(400, {'error': 'Controlla i campi della ricerca. ' + detail})
        except sqlite3.IntegrityError:
            self._reply(409, {'error': 'Esiste già una ricerca con questo nome. Scegli un nome diverso.'})
        except sqlite3.Error:
            self._reply(503, {'error': 'Database occupato. Attendi la fine della scansione e riprova.'})
        except ValueError as error:
            # Core validation errors contain field descriptions, not response bodies.
            message = str(error)
            if 'running' in message:
                self._reply(409, {'error': 'Scansione in corso. Attendi qualche istante e riprova.'})
            elif self.path in {'/api/ai/worth', '/api/ai/review'} or self.path.startswith('/api/ai/'):
                self._reply(400, {'error': message[:200]})
            else:
                self._reply(400, {'error': 'Controlla gli URL e i filtri. ' + message[:200]})
        except Exception:
            self._reply(503, {'error': 'Operazione non riuscita. Controlla la configurazione locale e riprova.'})

    def _action(self, conn, data):
        if self.path in {'/api/ai/worth', '/api/ai/review'}:
            if set(data) - {'site', 'id', 'refresh'} or type(data.get('refresh', False)) is not bool:
                raise ValueError('Richiesta di giudizio non valida.')
            if self.path == '/api/ai/review':
                from subitoo.ai.review import request
            else:
                from subitoo.ai.worth import request
            return request(conn, data.get('site'), data.get('id'), refresh=data.get('refresh', False))
        if self.path.startswith('/api/ai/'):
            from subitoo.ai.service import action
            return action(conn, self.path, data)
        if self.path in ('/api/watches/save', '/api/watches/quick-save'):
            spec = dict(data)
            watch_id = spec.pop('id', None)
            if watch_id is not None:
                if isinstance(watch_id, bool) or not isinstance(watch_id, int) or watch_id < 1:
                    raise ValueError('ID non valido')
                if not conn.execute('SELECT 1 FROM watches WHERE id=?', (watch_id,)).fetchone():
                    raise LookupError
            if self.path == '/api/watches/quick-save':
                from subitoo.core.searches import build_watch
                spec = build_watch(spec)
            return {'id': watches.save(conn, spec, watch_id=watch_id)}
        match = re.fullmatch(r'/api/watches/([1-9][0-9]*)/(toggle|remove)', self.path)
        if match:
            watch_id, action = int(match[1]), match[2]
            if not conn.execute('SELECT 1 FROM watches WHERE id=?', (watch_id,)).fetchone():
                raise LookupError
            if action == 'remove':
                watches.remove(conn, watch_id)
            else:
                if type(data.get('enabled')) is not bool:
                    raise ValueError('Attivazione non valida')
                spec = watches.get(conn, watch_id)
                watches.save(conn, {**spec, 'enabled': data['enabled']}, watch_id=watch_id)
            return {'ok': True}
        match = re.fullmatch(r'/api/queries/([1-9][0-9]*)/retry', self.path)
        if match:
            qid = int(match[1])
            if db.get_query(conn, qid) is None:
                raise LookupError
            recovery.manual_retry(conn, qid)
            return {'ok': True}
        if self.path == '/api/listings/flag':
            if set(data) - {'site', 'id', 'starred', 'hidden'}:
                raise ValueError('Richiesta non valida.')
            site, identity = data.get('site'), data.get('id')
            if site not in ('subito', 'vinted', 'wallapop') or not isinstance(identity, str) or not 1 <= len(identity) <= 200:
                raise ValueError('Identità annuncio non valida.')
            if 'starred' not in data and 'hidden' not in data:
                raise ValueError('Specifica almeno un’azione.')
            if ('starred' in data and type(data['starred']) is not bool) or ('hidden' in data and type(data['hidden']) is not bool):
                raise ValueError('Valore non valido.')
            if not conn.execute(
                'SELECT 1 FROM listings l JOIN queries q ON q.id=l.query_id WHERE q.site=? AND l.site_listing_id=? LIMIT 1',
                (site, identity),
            ).fetchone():
                raise LookupError
            cur = conn.execute(
                'SELECT starred, hidden FROM listing_flags WHERE site=? AND site_listing_id=?',
                (site, identity),
            ).fetchone()
            starred = int(data['starred']) if 'starred' in data else (cur['starred'] if cur else 0)
            hidden = int(data['hidden']) if 'hidden' in data else (cur['hidden'] if cur else 0)
            if starred or hidden:
                conn.execute(
                    'INSERT OR REPLACE INTO listing_flags(site, site_listing_id, starred, hidden, updated_at) VALUES (?,?,?,?,?)',
                    (site, identity, starred, hidden, db.now()),
                )
            else:
                conn.execute(
                    'DELETE FROM listing_flags WHERE site=? AND site_listing_id=?',
                    (site, identity),
                )
            return {'ok': True, 'starred': bool(starred), 'hidden': bool(hidden)}
        if self.path == '/api/listings/description':
            site, identity = data.get('site'), data.get('id')
            if site not in ('subito', 'vinted', 'wallapop') or not isinstance(identity, str) or not 1 <= len(identity) <= 200:
                raise ValueError('Identità annuncio non valida.')
            if not self.server.description_lock.acquire(blocking=False):
                raise ValueError('Attendi il caricamento della descrizione in corso.')
            try:
                return {'description': descriptions.load_description(conn, site, identity)}
            finally:
                self.server.description_lock.release()
        if self.path == '/api/notify/test':
            with self.server.notify_lock:
                if time.monotonic() - self.server.last_notify < 30:
                    raise ValueError('Attendi almeno 30 secondi tra le notifiche di prova.')
                self.server.last_notify = time.monotonic()
            from subitoo.core import notify
            notify.send(Notification(kind='new', title='Marketplace Watch — notifica di prova',
                                     url='https://ntfy.sh', query_name='Prova dalla GUI', site='',
                                     price=None))
            return {'ok': True}
        raise LookupError


def make_server(*, host='127.0.0.1', port=8787):
    if host != '127.0.0.1':
        raise ValueError('La GUI può ascoltare soltanto su 127.0.0.1')
    with closing(db.init_db()):
        pass
    server = ThreadingHTTPServer((host, port), GUIHandler)
    server.daemon_threads = True
    server.csrf_token = secrets.token_hex(32)
    server.notify_lock = threading.Lock()
    server.description_lock = threading.Lock()
    server.last_notify = float('-inf')
    return server
