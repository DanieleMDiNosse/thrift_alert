"""Declarative watch groups, implemented as ordinary independently seeded queries.

Notification ownership: the first successfully scanning, already-seeded query sends
one global new event. Later groups record a match silently; no retroactive pushes.
Only that first group's name is shown (not an unverified union of future scans).
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from subitoo.core import db
from subitoo.core.models import Filters
from subitoo.registry import get_site


class Source(BaseModel):
    model_config = ConfigDict(extra='forbid')
    search_url: str
    enabled: bool = True
    cron: str = '*/30 * * * *'
    max_pages: int = Field(default=1, ge=1, le=30)
    search_urls: list[str] = Field(default_factory=list, max_length=50)


class Watch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    search_text: str | None = Field(default=None, min_length=1, max_length=6050)
    search_phrases: list[str] = Field(default_factory=list, max_length=50)
    min_price_eur: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_price_eur: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    title_include: list[str] = Field(default_factory=list)
    title_exclude: list[str] = Field(default_factory=list)
    description_include: list[str] = Field(default_factory=list)
    notify_on_price_change: bool = False
    sources: dict[str, Source] = Field(min_length=1)

    @model_validator(mode='after')
    def valid_range(self):
        if not self.name.strip():
            raise ValueError('watch name cannot be blank')
        if any(not w.strip() for w in self.title_include + self.title_exclude + self.description_include):
            raise ValueError('keywords cannot be blank')
        if (self.min_price_eur is not None and self.max_price_eur is not None
                and self.min_price_eur > self.max_price_eur):
            raise ValueError('min_price_eur exceeds max_price_eur')
        for source in self.sources.values():
            if not croniter.is_valid(source.cron):
                raise ValueError('invalid source cron')
        return self


def _filters(w: Watch) -> Filters:
    # Literal, case-insensitive substrings. ALL include words, ANY exclude words.
    include = ''.join(f'(?=.*{re.escape(x)})' for x in w.title_include)
    if w.search_phrases:
        alternatives = []
        for phrase in w.search_phrases:
            words = phrase.split()
            if not words or len(phrase) > 120:
                raise ValueError('each search phrase must contain 1–120 characters')
            alternatives.append(''.join(f'(?=.*{re.escape(word)})' for word in words))
        include = '(?:' + '|'.join(alternatives) + ')' + include
    exclude = '|'.join(re.escape(x) for x in w.title_exclude)
    description = ''.join(f'(?=.*{re.escape(x.strip())})' for x in w.description_include)
    return Filters(price_min=w.min_price_eur, price_max=w.max_price_eur,
                   title_include=include or None, title_exclude=exclude or None,
                   description_include=description or None)


def get(conn, watch_id: int) -> dict[str, Any]:
    row = conn.execute('SELECT spec_json FROM watches WHERE id=?', (watch_id,)).fetchone()
    if row is None:
        raise ValueError(f'no watch {watch_id}')
    return json.loads(row['spec_json'])


def list_all(conn) -> list[dict[str, Any]]:
    return [{'id': r['id'], **json.loads(r['spec_json'])}
            for r in conn.execute('SELECT * FROM watches ORDER BY id')]


def queries(conn, watch_id: int):
    get(conn, watch_id)
    return [db.get_query(conn, r['query_id']) for r in conn.execute(
        'SELECT query_id FROM watch_sources WHERE watch_id=? ORDER BY site', (watch_id,))]


def save(conn, spec: dict, *, watch_id: int | None = None) -> int:
    w = Watch.model_validate(spec)
    filters = _filters(w)
    # All validation is done before any mutation. No network during configuration;
    # Subito's lazy resolve is already supported by the normal pipeline.
    searches = {}
    for site, source in w.sources.items():
        if not source.enabled:
            continue  # disabled Facebook can be documented without any session/adapter
        try:
            adapter = get_site(site)
        except KeyError:
            raise ValueError(f'{site}: unsupported adapter; leave disabled') from None
        search = adapter.validate_search({'url': source.search_url,
                                          'max_pages': str(source.max_pages)})
        if source.search_urls:
            search['urls'] = [adapter.validate_search({'url': url, 'max_pages': '1'})['url']
                              for url in source.search_urls]
        if w.search_text:
            search['deep_scan'] = True
            search['price_min'], search['price_max'] = w.min_price_eur, w.max_price_eur
        searches[site] = search
    if watch_id is not None:
        get(conn, watch_id)
    conn.execute('BEGIN IMMEDIATE')
    try:
        if watch_id is None:
            watch_id = conn.execute('INSERT INTO watches(name,spec_json) VALUES (?,?)',
                                    (w.name, w.model_dump_json())).lastrowid
        else:
            conn.execute('UPDATE watches SET name=?,spec_json=? WHERE id=?',
                         (w.name, w.model_dump_json(), watch_id))
        old = {r['site']: r['query_id'] for r in conn.execute(
            'SELECT site,query_id FROM watch_sources WHERE watch_id=?', (watch_id,))}
        for site, qid in old.items():
            current = db.get_query(conn, qid)
            if current.status.value == 'running':
                raise ValueError('cannot edit a watch with a running source; try again later')
            if site not in w.sources:
                db.delete_query(conn, qid)
            elif not w.sources[site].enabled:
                db.update_query(conn, qid, enabled=False)
        for site, search in searches.items():
            source = w.sources[site]
            q = db.get_query(conn, old[site]) if site in old else None
            if q and (q.search != search or q.filters != filters):
                if q.status.value == 'running':
                    raise ValueError('cannot edit a running source; try again later')
                db.delete_query(conn, q.id)
                q = None  # changed match definition gets a fresh silent baseline
            if q:
                db.update_query(conn, q.id, name=f"watch:{watch_id}:{site}", enabled=w.enabled,
                                cron=source.cron, notify_on_price_change=w.notify_on_price_change)
            else:
                qid = db.create_query(conn, name=f"watch:{watch_id}:{site}", site=site, search=search,
                                      filters=filters, cron=source.cron, enabled=w.enabled,
                                      notify_on_price_change=w.notify_on_price_change)
                conn.execute('INSERT INTO watch_sources VALUES (?,?,?)', (watch_id, site, qid))
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise
    return watch_id


def remove(conn, watch_id: int) -> None:
    owned = queries(conn, watch_id)
    conn.execute('BEGIN IMMEDIATE')
    try:
        for q in owned:
            if q.status.value == 'running':
                raise ValueError('cannot remove a running source')
            db.delete_query(conn, q.id)
        conn.execute('DELETE FROM watches WHERE id=?', (watch_id,))
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise
