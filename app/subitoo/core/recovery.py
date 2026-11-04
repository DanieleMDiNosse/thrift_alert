"""Durable, conservative recovery: retry transport failures, respect server limits.

A 403/CAPTCHA or malformed response is never automatically retried; unknown
failures back off and retry so a monitor never stays silently stuck. HTTP 429
pauses every query on that marketplace; callers cannot bypass its cooldown.
No credentials, response bodies or private URLs are persisted here.
"""
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import math
import re
import time

from subitoo.core import db


class HTTPFailure(RuntimeError):
    def __init__(self, site: str, status: int, *, retry_after: int = 0):
        super().__init__(f'{site}: request HTTP {status}')
        self.status = status
        self.retry_after = max(0, retry_after)


class DescriptionPending(RuntimeError):
    """An observed catalog needs another bounded description batch."""


@dataclass(frozen=True)
class Failure:
    code: str
    retryable: bool


def retry_after_seconds(value: str | None, *, now_ts: int | None = None) -> int:
    if not value:
        return 0
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        try:
            return max(0, math.ceil(parsedate_to_datetime(value).timestamp() -
                                    (time.time() if now_ts is None else now_ts)))
        except (ValueError, TypeError, OverflowError):
            return 0


def classify(error: BaseException | str) -> Failure:
    text = str(error).casefold()
    status = getattr(error, 'status', None)
    if status == 429 or re.search(r'\b(?:http\s*|status[=: ]+)429\b', text):
        return Failure('rate_limited', True)
    if status in (401, 403) or re.search(r'\bhttp\s*(401|403)\b', text) or any(
            term in text for term in ('captcha', 'access denied', 'access_denied')):
        return Failure('access_denied', False)
    if isinstance(error, DescriptionPending):
        return Failure('description_pending', True)
    if any(term in text for term in ('interruptedscan', 'targetclosederror', 'browser has been closed',
                                      'context or browser has been closed', 'browser closed')):
        return Failure('browser_closed', True)
    if any(term in text for term in ('timeouterror', 'operation timed out', 'curl: (28)',
                                      'timeout:', 'timeout ')):
        return Failure('timeout', True)
    if any(term in text for term in ('dnserror', 'ns_error_unknown_host', 'could not resolve',
                                      'ns_error_connection', 'ns_error_net', 'ns_error_abort',
                                      'connectionerror', 'connection refused', 'connection reset',
                                      'curl: (6)', 'curl: (7)', 'curl: (35)', 'curl: (56)')):
        return Failure('network', True)
    if status in (408, 500, 502, 503, 504) or re.search(r'\bhttp\s*(408|500|502|503|504)\b', text):
        return Failure('temporary_server', True)
    if any(term in text for term in ('malformed', 'flight payload', 'invalid listing',
                                      'missing product', 'could not parse', 'schema')):
        return Failure('invalid_response', False)
    # Unknown failures back off and retry: a monitor must not stay silently stuck.
    return Failure('fetch_error', True)


def snapshot(conn, query_id: int) -> dict | None:
    row = conn.execute('SELECT error_code,failure_count,retry_at FROM query_recovery WHERE query_id=?',
                       (query_id,)).fetchone()
    return dict(row) if row else None


def site_retry_at(conn, site: str) -> int:
    row = conn.execute('SELECT retry_at FROM site_backoff WHERE site=?', (site,)).fetchone()
    return row[0] if row else 0


def record_failure(conn, query_id: int, error: BaseException | str, *, now_ts: int) -> dict:
    failure = classify(error)
    old = snapshot(conn, query_id)
    count = old['failure_count'] + 1 if old and old['error_code'] == failure.code else 1
    retry_at = None
    if failure.retryable:
        if failure.code == 'description_pending':
            delay = 300
        else:
            base = 3600 if failure.code == 'rate_limited' else 300
            cap = 86400 if failure.code == 'rate_limited' else 21600
            delay = min(cap, base * 2 ** min(count - 1, 10))
        retry_at = now_ts + max(delay, getattr(error, 'retry_after', 0))
    conn.execute('INSERT OR REPLACE INTO query_recovery VALUES (?,?,?,?,?)',
                 (query_id, failure.code, count, retry_at, now_ts))
    if failure.code == 'rate_limited':
        site = db.get_query(conn, query_id).site
        conn.execute('INSERT INTO site_backoff VALUES (?,?) ON CONFLICT(site) DO UPDATE '
                     'SET retry_at=MAX(site_backoff.retry_at,excluded.retry_at)', (site, retry_at))
    return snapshot(conn, query_id)


def limit_site(conn, site: str, error: HTTPFailure, *, now_ts: int) -> None:
    until = now_ts + max(3600, error.retry_after)
    conn.execute('INSERT INTO site_backoff VALUES (?,?) ON CONFLICT(site) DO UPDATE '
                 'SET retry_at=MAX(site_backoff.retry_at,excluded.retry_at)', (site, until))


def clear(conn, query_id: int) -> None:
    conn.execute('DELETE FROM query_recovery WHERE query_id=?', (query_id,))


def recover_due(conn, *, now_ts: int) -> None:
    # Adopt old latched errors once, retaining baseline and all observed identities.
    legacy = conn.execute('''SELECT q.id,r.error_message,q.status_changed_at FROM queries q
        JOIN runs r ON r.id=(SELECT MAX(id) FROM runs WHERE query_id=q.id)
        LEFT JOIN query_recovery f ON f.query_id=q.id
        WHERE q.enabled=1 AND q.status='error' AND f.query_id IS NULL AND r.status='error' ''').fetchall()
    for row in legacy:
        record_failure(conn, row['id'], row['error_message'] or '', now_ts=row['status_changed_at'])
    conn.execute("UPDATE queries SET status='pending',status_changed_at=? WHERE enabled=1 AND status='error' "
                 'AND id IN (SELECT query_id FROM query_recovery WHERE retry_at<=?)', (now_ts, now_ts))


def ready(conn, query_id: int, site: str, *, now_ts: int) -> bool:
    state = snapshot(conn, query_id)
    return site_retry_at(conn, site) <= now_ts and not (
        state and state['retry_at'] is not None and state['retry_at'] > now_ts)


def manual_retry(conn, query_id: int, *, now_ts: int | None = None) -> None:
    now_ts = db.now() if now_ts is None else now_ts
    q = db.get_query(conn, query_id)
    if q is None:
        raise LookupError('Ricerca non trovata.')
    state = snapshot(conn, query_id)
    if site_retry_at(conn, q.site) > now_ts or (state and state['error_code'] == 'rate_limited'
                                               and state['retry_at'] > now_ts):
        raise ValueError('Mercatino in pausa per limite richieste: attendi la fine del cooldown.')
    if q.status.value == 'error':
        clear(conn, query_id)
        conn.execute("UPDATE queries SET status='pending',status_changed_at=? WHERE id=? AND status='error'",
                     (now_ts, query_id))
