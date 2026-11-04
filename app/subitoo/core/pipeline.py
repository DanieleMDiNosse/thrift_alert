"""The run pipeline: dispatcher (run_due) -> per-query run -> per-listing processing.

See SPEC.md section 8. Key properties:
- first run silently seeds (no notifications), then alerts on new + price changes;
- notify-then-persist gives at-least-once delivery that self-heals via retry;
- one failing query latches to 'error' and never blocks the others;
- a watchdog reclaims queries stuck in 'running' after a crash.
"""

from __future__ import annotations

import inspect
import logging
import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from croniter import croniter

from subitoo.config import get_settings
from subitoo.core import db, notify, recovery
from subitoo.core.privacy import safe_error
from subitoo.core.fetch import build_context
from subitoo.core.filters import apply_filters
from subitoo.core.searching import QueryScan, fetch_query
from subitoo.core.models import Listing, Notification, Query, QueryStatus
from subitoo.registry import get_site

log = logging.getLogger("subitoo.pipeline")
DESCRIPTION_BATCH_SIZE = 5
DESCRIPTION_REQUEST_GAP = 10.0
# Per-item description failures: retry after 1h, 4h, 16h, then give up on that item.
DESCRIPTION_ITEM_MAX_ATTEMPTS = 3
DESCRIPTION_ITEM_RETRY_BASE = 3600
# This many item failures with zero successes in one run means the site changed (or
# blocks every item page), not that the items are bad: fail the query loudly instead.
DESCRIPTION_SYSTEMIC_FAILURES = 3

# Injection seam so tests can substitute a fake sender.
SendFn = Callable[[Notification], None]
SleepFn = Callable[[float], None]


def cron_due(cron_expr: str, last_run_at: int | None, now_ts: int, tz: str) -> bool:
    """Is this cron due at now_ts, given the last run time?

    A query with no prior run is due immediately (its first run). Otherwise it is due
    if the next fire time after ``last_run_at`` is at or before ``now_ts``.
    """
    tzinfo = ZoneInfo(tz)
    if last_run_at is None:
        return True
    base = datetime.fromtimestamp(last_run_at, tzinfo)
    nxt = croniter(cron_expr, base).get_next(datetime)
    return nxt.timestamp() <= now_ts


@dataclass
class RunResult:
    n_found: int = 0
    n_new: int = 0
    n_price_changed: int = 0
    n_notified: int = 0
    n_suppressed: int = 0  # notifications skipped as same-heartbeat cross-query dupes


def run_due(conn, *, now_ts: int | None = None, send: SendFn = notify.send,
            sleep: SleepFn = time.sleep, should_stop: Callable[[], bool] = lambda: False) -> list[int]:
    """Ofelia entrypoint (one heartbeat). Returns the ids of queries actually run."""
    settings = get_settings()
    fixed_clock = now_ts is not None
    now_ts = now_ts or db.now()

    stale = db.reclaim_stale(conn, settings.run_timeout, now_ts)
    for qid in stale:
        log.warning("reclaimed stale query %s -> error", qid)
    recovery.recover_due(conn, now_ts=now_ts)

    # Cross-query dedup scratchpad, scoped to THIS heartbeat: the set of
    # (site, site_listing_id) ads already notified during this run_due pass. When two
    # overlapping queries match the same ad on the same heartbeat, the first to notify
    # wins and the rest suppress their *send* (they still persist their own state).
    # It's born here and dies when the heartbeat ends — nothing spans heartbeats.
    heartbeat_seen: set[tuple[str, str]] = set()

    ran: list[int] = []
    for q in db.enabled_pending_queries(conn):
        if should_stop():
            break
        current_ts = now_ts if fixed_clock else db.now()
        if not get_site(q.site).enabled:
            continue  # site parked in code (kill switch) — leave the query untouched
        state = recovery.snapshot(conn, q.id)
        if not recovery.ready(conn, q.id, q.site, now_ts=current_ts):
            continue
        if not state and not cron_due(q.cron, q.last_run_at, current_ts, settings.tz):
            continue
        if not db.claim_query(conn, q.id, current_ts):
            continue  # someone else grabbed it
        run_query(conn, q.id, now_ts=current_ts, send=send, sleep=sleep,
                  heartbeat_seen=heartbeat_seen)
        ran.append(q.id)
    return ran


def run_query(conn, query_id: int, *, now_ts: int | None = None,
              send: SendFn = notify.send, sleep: SleepFn = time.sleep,
              heartbeat_seen: set[tuple[str, str]] | None = None) -> RunResult:
    """Execute one query end to end. Assumes the query is already claimed (running).

    ``heartbeat_seen`` is the cross-query dedup scratchpad shared across all queries in
    one ``run_due`` heartbeat (see there). A standalone run (CLI ``run``/``retry``) is a
    single query, so it just gets a fresh empty set and dedups against nobody.
    """
    now_ts = now_ts or db.now()
    started_clock = time.monotonic()
    if heartbeat_seen is None:
        heartbeat_seen = set()
    q = db.get_query(conn, query_id)
    if q is None:
        raise KeyError(f"query {query_id} not found")

    if recovery.site_retry_at(conn, q.site) > now_ts:
        if q.status == QueryStatus.RUNNING:
            db.set_status(conn, query_id, QueryStatus.PENDING, now_ts)
        return RunResult()  # direct/manual callers must also respect server cooldowns
    run_id = db.start_run(conn, query_id, now_ts)
    res = RunResult()
    try:
        adapter = get_site(q.site)
        ctx = build_context(get_settings(), needs_browser=adapter.needs_browser)
        # Human pacing: pause before touching the site so a heartbeat with several due
        # queries doesn't fire them in a burst. The base is `run_delay_seconds`; we
        # add a fresh 0–1000ms of jitter each run so the cadence isn't a metronome.
        # Skipped on the seeding run — the interactive `add` seeds with the user
        # waiting, and a first-run baseline shouldn't stall (migration 0002).
        if q.seeded and q.run_delay_seconds > 0:
            sleep(q.run_delay_seconds + random.randint(0, 1000) / 1000)
        # One listing per id: paging a live result set (new ads shift the order between
        # page requests) can return the same ad twice, which would break the seed insert.
        stored = conn.execute('SELECT * FROM description_scans WHERE query_id=?', (q.id,)).fetchone()
        if stored:
            scan = QueryScan([Listing(**item) for item in json.loads(stored['snapshot_json'])],
                             json.loads(stored['coverage_json']), bool(stored['expanded_baseline']))
            observed_ts = stored['scanned_at']
        else:
            scan = fetch_query(conn, q, adapter, ctx, sleep=sleep)
            observed_ts = now_ts
            if q.site == 'vinted' and q.filters.description_include:
                conn.execute('INSERT INTO description_scans '
                             '(query_id,snapshot_json,coverage_json,expanded_baseline,scanned_at) VALUES (?,?,?,?,?)',
                             (q.id, json.dumps([asdict(item) for item in scan.listings]),
                              json.dumps(scan.coverage), int(scan.expanded_baseline), observed_ts))
        listings = scan.listings
        baseline = not q.seeded or scan.expanded_baseline
        stats: dict = {}
        matched = _matches(conn, q, listings, sleep=sleep, baseline=baseline,
                           stats=stats, now_ts=now_ts, ctx=ctx)
        res.n_found = len(matched)
        verified_ids = stats.get('verified', set())
        # Items unverifiable during the baseline join it silently once verified.
        deferred = {row[0] for row in conn.execute(
            'SELECT site_listing_id FROM description_deferred WHERE query_id=?', (query_id,))} & verified_ids

        if baseline:
            # FIRST RUN: record everything as seen, notify nothing.
            conn.execute("BEGIN IMMEDIATE")
            try:
                for lst in matched:
                    existing = db.get_listing(conn, query_id, lst.site_listing_id)
                    if existing is None:
                        db.insert_listing(conn, query_id, lst, now_ts)
                    else:
                        db.update_listing_price(conn, existing["id"], lst.price, now_ts)
                    conn.execute(
                        "INSERT OR IGNORE INTO listing_events VALUES (?,?,'baseline',?)",
                        (q.site, lst.site_listing_id, now_ts))
                db.update_query(conn, query_id, seeded=True)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        else:
            for lst in matched:
                if lst.site_listing_id in deferred and db.get_listing(conn, query_id, lst.site_listing_id) is None:
                    db.insert_listing(conn, query_id, lst, now_ts)
                    continue
                _process_listing(conn, q, lst, res, now_ts, send, heartbeat_seen)
        for listing_id in deferred:
            conn.execute('DELETE FROM description_deferred WHERE query_id=? AND site_listing_id=?',
                         (query_id, listing_id))

        conn.execute("INSERT OR REPLACE INTO scan_coverage (query_id,coverage_json,scanned_at,description_skipped) "
                     "VALUES (?,?,?,?)",
                     (query_id, json.dumps(scan.coverage), observed_ts, stats.get('skipped', 0)))
        conn.execute('DELETE FROM description_scans WHERE query_id=?', (query_id,))
        finished_ts = now_ts + max(0, int(time.monotonic() - started_clock))
        db.finish_run(conn, run_id, status="ok", n_found=res.n_found, n_new=res.n_new,
                      n_price_changed=res.n_price_changed, n_notified=res.n_notified,
                      ts=finished_ts)
        db.update_query(conn, query_id, last_run_at=finished_ts)
        db.set_status(conn, query_id, QueryStatus.PENDING, finished_ts)
        recovery.clear(conn, query_id)
    except Exception as e:  # noqa: BLE001 - latch to error, never crash the dispatcher
        diagnostic = safe_error(e)
        if isinstance(e, recovery.DescriptionPending):
            log.info('query %s: descriptions deferred to the next batch', query_id)
        else:
            log.error("query %s failed: %s", query_id, diagnostic)
        finished_ts = now_ts + max(0, int(time.monotonic() - started_clock))
        recovery.record_failure(conn, query_id, e, now_ts=finished_ts)
        db.finish_run(conn, run_id, status="error", error_message=diagnostic, ts=finished_ts)
        db.set_status(conn, query_id, QueryStatus.ERROR, finished_ts)
    return res


def _item_failure_active(conn, site: str, listing_id: str, now_ts: int) -> bool:
    row = conn.execute('SELECT retry_at FROM description_failures WHERE site=? AND site_listing_id=?',
                       (site, listing_id)).fetchone()
    return row is not None and (row['retry_at'] is None or row['retry_at'] > now_ts)


def _record_item_failure(conn, site: str, listing_id: str, kind: str, now_ts: int) -> None:
    row = conn.execute('SELECT attempts FROM description_failures WHERE site=? AND site_listing_id=?',
                       (site, listing_id)).fetchone()
    attempts = (row['attempts'] if row else 0) + 1
    retry_at = None
    if kind != 'gone' and attempts < DESCRIPTION_ITEM_MAX_ATTEMPTS:
        retry_at = now_ts + DESCRIPTION_ITEM_RETRY_BASE * 4 ** (attempts - 1)
    conn.execute('INSERT OR REPLACE INTO description_failures VALUES (?,?,?,?,?,?)',
                 (site, listing_id, kind, attempts, retry_at, now_ts))


def _matches(conn, q: Query, listings, *, sleep: SleepFn = time.sleep,
             cache_descriptions: bool = True, baseline: bool = False,
             stats: dict | None = None, now_ts: int | None = None, ctx=None):
    """Filter listings. Description-dependent filters fetch Vinted details in batches.

    One unreadable item never blocks the query: it is excluded (cannot be verified),
    retried later, and — if seen during a baseline — joins the baseline silently once
    verified. Site-wide failures (429/403/5xx/network) still propagate.
    """
    stats = {} if stats is None else stats
    stats.setdefault('skipped', 0)
    stats.setdefault('verified', set())
    # Cheap criteria first; never fan out detail requests for rejected candidates.
    basic = q.filters.model_copy(update={'description_include': None})
    matched = apply_filters(listings, basic)
    if (q.filters.price_min is not None or q.filters.price_max is not None) and conn.execute(
            "SELECT 1 FROM watch_sources WHERE query_id=?", (q.id,)).fetchone():
        matched = [x for x in matched if x.currency == "EUR"]
    if q.filters.description_include:
        from subitoo.core import descriptions
        from dataclasses import replace
        now_ts = db.now() if now_ts is None else now_ts

        def cached_text(item):
            row = conn.execute('SELECT description FROM listing_descriptions WHERE site=? AND site_listing_id=?',
                               (q.site, item.site_listing_id)).fetchone()
            return row['description'] if row else None

        def defer(item):
            stats['skipped'] += 1
            if cache_descriptions and baseline:
                conn.execute('INSERT OR IGNORE INTO description_deferred VALUES (?,?)',
                             (q.id, item.site_listing_id))

        def verified(item):
            stats['verified'].add(item.site_listing_id)

        needs_fetch = [item for item in matched if q.site == 'vinted'
                       and descriptions.native_description(q.site, json.dumps(item.raw)) is None
                       and cached_text(item) is None]
        prior_ids = {
            row[0] for row in conn.execute(
                'SELECT site_listing_id FROM description_failures WHERE site=? '
                'UNION SELECT site_listing_id FROM description_deferred WHERE query_id=?',
                (q.site, q.id))
        }
        unresolved = [item for item in needs_fetch
                      if descriptions.can_load(q.site, item.site_listing_id, item.url)
                      and not (cache_descriptions and _item_failure_active(conn, q.site, item.site_listing_id, now_ts))]
        # Fresh items first so a retry backlog never delays newly published listings.
        unresolved.sort(key=lambda item: item.site_listing_id in prior_ids)
        remaining = len(unresolved)
        fetched_count = successes = 0
        failures: list[tuple] = []  # (item, kind), recorded only if not systemic
        fetched_texts: dict[str, str] = {}
        fetch_fn = descriptions.fetch_vinted_description
        pass_ctx = ctx is not None and 'ctx' in inspect.signature(fetch_fn).parameters

        def settle_failures():
            item_level = [kind for _, kind in failures if kind != 'gone']
            if len(item_level) >= DESCRIPTION_SYSTEMIC_FAILURES and successes == 0:
                raise RuntimeError(
                    'vinted: description schema changed; could not parse item pages' if item_level[-1] == 'parse'
                    else 'vinted: description pages redirect away from items')
            if cache_descriptions:
                for failed, kind in failures:
                    _record_item_failure(conn, q.site, failed.site_listing_id, kind, now_ts)
            failures.clear()

        if cache_descriptions:
            conn.execute('UPDATE description_scans SET pending_count=? WHERE query_id=?', (remaining, q.id))
        for item in unresolved:
            if item.site_listing_id in fetched_texts or cached_text(item) is not None:
                continue
            if cache_descriptions and fetched_count >= DESCRIPTION_BATCH_SIZE:
                settle_failures()
                raise recovery.DescriptionPending('Descrizioni in attesa: ripresa automatica a piccoli gruppi.')
            # Same safe item-bound fetch as the manual description action.
            sleep(DESCRIPTION_REQUEST_GAP)
            fetched_count += 1
            remaining -= 1
            try:
                text = (fetch_fn(item.url, item.site_listing_id, ctx=ctx)
                        if pass_ctx else fetch_fn(item.url, item.site_listing_id))
            except descriptions.DescriptionUnavailable as error:
                log.warning('query %s: description of %s unavailable (%s)',
                            q.id, item.site_listing_id, error.kind)
                failures.append((item, error.kind))
                if cache_descriptions:
                    if baseline:
                        conn.execute('INSERT OR IGNORE INTO description_deferred VALUES (?,?)',
                                     (q.id, item.site_listing_id))
                    conn.execute('UPDATE description_scans SET pending_count=? WHERE query_id=?',
                                 (remaining, q.id))
                continue
            successes += 1
            fetched_texts[item.site_listing_id] = text
            if cache_descriptions:
                conn.execute('INSERT OR REPLACE INTO listing_descriptions VALUES (?,?,?,?)',
                             (q.site, item.site_listing_id, text, db.now()))
                conn.execute('DELETE FROM description_failures WHERE site=? AND site_listing_id=?',
                             (q.site, item.site_listing_id))
                conn.execute('UPDATE description_scans SET pending_count=? WHERE query_id=?', (remaining, q.id))
        settle_failures()

        enriched = []
        for item in matched:
            text = descriptions.native_description(q.site, json.dumps(item.raw))
            if text is None and q.site == 'vinted':
                text = fetched_texts.get(item.site_listing_id)
                if text is None:
                    text = cached_text(item)
                if text is None:
                    defer(item)
                    continue
            if text is not None:
                if q.site == 'vinted':
                    verified(item)
                key = 'body' if q.site == 'subito' else 'description'
                item = replace(item, raw={**item.raw, key: text})
            enriched.append(item)
        matched = apply_filters(enriched, q.filters)
    return matched


def _notification_name(conn, q: Query) -> str:
    row = conn.execute("SELECT w.name FROM watches w JOIN watch_sources s "
                       "ON s.watch_id=w.id WHERE s.query_id=?", (q.id,)).fetchone()
    return row["name"] if row else q.name


def _process_listing(conn, q: Query, lst, res: RunResult, now_ts: int,
                     send: SendFn, heartbeat_seen: set[tuple[str, str]]) -> None:
    """Classify one already-filtered listing and, if warranted, notify-then-persist."""
    settings = get_settings()
    existing = db.get_listing(conn, q.id, lst.site_listing_id)

    pending = conn.execute(
        "SELECT 1 FROM listing_events WHERE site=? AND site_listing_id=? AND state='pending'",
        (q.site, lst.site_listing_id)).fetchone()
    if existing is not None and pending:
        _notify_and_persist(conn, q, lst, kind="new", old_price=None, res=res,
                            now_ts=now_ts, send=send, settings=settings,
                            existing_id=existing["id"], heartbeat_seen=heartbeat_seen)
        return

    if existing is None:
        _notify_and_persist(conn, q, lst, kind="new", old_price=None, res=res,
                            now_ts=now_ts, send=send, settings=settings,
                            heartbeat_seen=heartbeat_seen)
        return

    if lst.price == existing["last_price"]:
        db.touch_listing(conn, existing["id"], now_ts)  # unchanged -> just seen
        return

    if not q.notify_on_price_change:
        db.update_listing_price(conn, existing["id"], lst.price, now_ts)
        return

    # price changed and (since it passed filters) still matches -> notify.
    _notify_and_persist(conn, q, lst, kind="price_change",
                        old_price=existing["last_price"], res=res, now_ts=now_ts,
                        send=send, settings=settings, existing_id=existing["id"],
                        heartbeat_seen=heartbeat_seen)


def _notify_and_persist(conn, q: Query, lst, *, kind: str, old_price, res: RunResult,
                        now_ts: int, send: SendFn, settings,
                        heartbeat_seen: set[tuple[str, str]],
                        existing_id: int | None = None) -> None:
    def persist() -> None:
        """Record this query's own view of the listing. Runs whether we sent or
        suppressed — the dedup gates the *send*, never the per-query state, so the
        query's diary stays honest and it won't re-fire the ad on the next heartbeat."""
        if kind == "new":
            if existing_id is None:
                db.insert_listing(conn, q.id, lst, now_ts)
            else:
                db.update_listing_price(conn, existing_id, lst.price, now_ts)
            res.n_new += 1
        else:
            db.update_listing_price(conn, existing_id, lst.price, now_ts)
            res.n_price_changed += 1

    def log_notif(status: str, error: str | None = None) -> None:
        db.log_notification(conn, query_id=q.id, site_listing_id=lst.site_listing_id,
                            channel=settings.default_channel, kind=kind,
                            status=status, error=error, ts=now_ts)

    # Cross-query dedup: another query on this heartbeat already notified this exact ad.
    # Skip the send (and its image download) but still persist our own state.
    key = (q.site, lst.site_listing_id)
    if kind == "new":
        # Serialize the global event check and send across independent CLI processes.
        # The transaction is held over the bounded network call. A crash after remote
        # acceptance but before commit can still duplicate: remote exactly-once is not
        # possible without notifier idempotency. Rollback leaves the event retryable.
        conn.execute("BEGIN IMMEDIATE")
        try:
            event = conn.execute(
                "SELECT state FROM listing_events WHERE site=? AND site_listing_id=?",
                key).fetchone()
            if event and event["state"] in ("baseline", "sent"):
                persist()
                log_notif("suppressed")
                res.n_suppressed += 1
                conn.execute("COMMIT")
                return
            conn.execute("INSERT OR IGNORE INTO listing_events VALUES (?,?,'pending',?)",
                         (*key, now_ts))
            n = Notification(kind="new", title=lst.title, url=lst.url,
                             query_name=_notification_name(conn, q), site=q.site, price=lst.price,
                             currency=lst.currency, image_url=lst.image_url,
                             location=lst.location, shipping_available=lst.shipping_available)
            try:
                send(n)
            except Exception:
                log_notif("failed", "notification transport failed; event remains pending")
                conn.execute("COMMIT")
                return
            conn.execute("UPDATE listing_events SET state='sent' "
                         "WHERE site=? AND site_listing_id=?", key)
            persist()
            log_notif("sent")
            conn.execute("COMMIT")
            res.n_notified += 1
            heartbeat_seen.add(key)
            return
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    if key in heartbeat_seen:
        persist()
        log_notif("suppressed")
        res.n_suppressed += 1
        return

    n = Notification(
        kind=kind, title=lst.title, url=lst.url, query_name=_notification_name(conn, q), site=q.site,
        price=lst.price, currency=lst.currency, image_url=lst.image_url,
        location=lst.location, shipping_available=lst.shipping_available,
        old_price=old_price,
    )
    try:
        send(n)
    except Exception as e:  # noqa: BLE001 - do NOT persist -> retried next heartbeat
        log.warning("notify failed for %s (%s): %s", lst.site_listing_id, kind, safe_error(e))
        log_notif("failed", safe_error(e))
        return

    # send succeeded -> persist the state change, log it, and claim this ad for the
    # rest of the heartbeat so overlapping queries suppress. Claiming ONLY on success
    # preserves at-least-once: a failed send leaves the ad unclaimed for the next query.
    persist()
    log_notif("sent")
    res.n_notified += 1
    heartbeat_seen.add(key)


def dry_run(conn, query_id: int):
    """Fetch + filter only. No notify, no persist, no seeding. For `query test`."""
    q = db.get_query(conn, query_id)
    if q is None:
        raise KeyError(f"query {query_id} not found")
    if recovery.site_retry_at(conn, q.site) > db.now():
        raise ValueError('Mercatino in pausa per limite richieste: attendi la ripresa automatica.')
    adapter = get_site(q.site)
    ctx = build_context(get_settings(), needs_browser=adapter.needs_browser)
    scan = fetch_query(conn, q, adapter, ctx)
    return _matches(conn, q, scan.listings, cache_descriptions=False, ctx=ctx)
