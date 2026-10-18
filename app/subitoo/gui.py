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
from urllib.parse import urlsplit

from pydantic import ValidationError

from subitoo.config import get_settings
from subitoo.core import db, watches
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


def _monitor_running():
    path = Path(get_settings().db_path + '.dispatcher.lock')
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


def _state(conn):
    groups = watches.list_all(conn)
    for group in groups:
        group['queries'] = []
        for query in watches.queries(conn, group['id']):
            group['queries'].append({
                'id': query.id, 'site': query.site, 'enabled': query.enabled,
                'seeded': query.seeded, 'status': query.status.value,
                'last_run_at': query.last_run_at,
                # Do not forward exception text from legacy/raw historical rows.
                'error': 'Scansione sospesa dopo un errore. Controlla accesso e URL prima di riprovare.'
                         if query.status.value == 'error' else None,
            })
    rows = conn.execute('''
        WITH observed AS (
            SELECT l.title,l.last_price AS price,l.currency,l.url,q.site,
                   COALESCE(w.name,q.name) AS watch_name,l.first_seen_at,l.last_seen_at,
                   ROW_NUMBER() OVER (PARTITION BY q.site,l.site_listing_id
                                      ORDER BY l.last_seen_at DESC,l.id DESC) AS position
            FROM listings l JOIN queries q ON q.id=l.query_id
            LEFT JOIN watch_sources s ON s.query_id=q.id
            LEFT JOIN watches w ON w.id=s.watch_id
        ) SELECT title,price,currency,url,site,watch_name,first_seen_at,last_seen_at
          FROM observed WHERE position=1 ORDER BY last_seen_at DESC LIMIT 40
    ''').fetchall()
    listings = [{**dict(row), 'url': _safe_link(row['url'])} for row in rows]
    try:
        from subitoo.core.notify import active_notifier
        active_notifier().validate_config()
        configured = True
    except Exception:
        configured = False
    return {'watches': groups, 'listings': listings, 'monitor_running': _monitor_running(),
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
                         "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
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
        if self.path == '/api/state':
            try:
                with closing(db.init_db()) as conn:
                    # One WAL snapshot across groups, sources and listings. A parallel
                    # delete/edit must not invalidate subsequent reads in this response.
                    conn.execute('BEGIN')
                    state = _state(conn)
                    conn.commit()
                self._reply(200, state)
            except sqlite3.Error:
                self._reply(503, {'error': 'Database occupato o non disponibile. Riprova tra poco.'})
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/static/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/static/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path not in assets:
            self._reply(404, {'error': 'Pagina non trovata.'})
            return
        filename, mime = assets[self.path]
        text = (ASSETS / filename).read_text(encoding='utf-8')
        if filename == 'index.html':
            text = text.replace('__CSRF_TOKEN__', self.server.csrf_token)
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
            else:
                self._reply(400, {'error': 'Controlla gli URL e i filtri. ' + message[:200]})
        except Exception:
            self._reply(503, {'error': 'Operazione non riuscita. Controlla la configurazione locale e riprova.'})

    def _action(self, conn, data):
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
            conn.execute("UPDATE queries SET status='pending',status_changed_at=? WHERE id=? AND status='error'",
                         (db.now(), qid))
            return {'ok': True}
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
    server.last_notify = float('-inf')
    return server
