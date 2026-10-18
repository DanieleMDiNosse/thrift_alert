"""Small JSON-first watch management CLI; no extra configuration dependencies."""
import json
import sqlite3
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from subitoo.core import db, pipeline, watches

watch_app = typer.Typer(no_args_is_help=True, help='Unified saved watch groups (JSON)')
console = Console()


def _save(path: Path, watch_id: int | None = None):
    try:
        with db.init_db() as conn:
            wid = watches.save(conn, json.loads(path.read_text()), watch_id=watch_id)
        console.print(f'Watch {wid} saved; new/changed sources seed silently on first scan.')
    except (ValueError, KeyError, OSError, sqlite3.Error) as e:
        raise typer.BadParameter(str(e)) from None


@watch_app.command('add')
def add(from_json: Path = typer.Option(..., '--from-json')):
    """Add one watch definition. No network calls."""
    _save(from_json)


@watch_app.command('edit')
def edit(watch_id: int, from_json: Path = typer.Option(..., '--from-json')):
    """Replace definition; changed match rules get a new silent baseline."""
    _save(from_json, watch_id)


@watch_app.command('list')
def list_watches():
    console.print_json(json.dumps(watches.list_all(db.init_db())))


@watch_app.command('show')
def show(watch_id: int):
    try:
        conn = db.init_db()
        console.print_json(json.dumps({'definition': watches.get(conn, watch_id),
            'queries': [{'id': q.id, 'site': q.site, 'seeded': q.seeded,
                         'status': q.status.value, 'enabled': q.enabled}
                        for q in watches.queries(conn, watch_id)]}))
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None


def _enabled(watch_id: int, value: bool):
    conn = db.init_db()
    try:
        spec = watches.get(conn, watch_id)
        watches.save(conn, {**spec, 'enabled': value}, watch_id=watch_id)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None
    console.print(f'Watch {watch_id} enabled={value}')


@watch_app.command('enable')
def enable(watch_id: int):
    _enabled(watch_id, True)


@watch_app.command('disable')
def disable(watch_id: int):
    _enabled(watch_id, False)


@watch_app.command('rm')
def remove(watch_id: int, yes: bool = typer.Option(False, '--yes', '-y')):
    if not yes and not typer.confirm(f'Delete watch {watch_id} and its source queries?'):
        raise typer.Abort()
    try:
        watches.remove(db.init_db(), watch_id)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None
    console.print(f'Deleted watch {watch_id}')


@watch_app.command('test')
def test(watch_id: int):
    """Fetch/filter each active source without writes, notifications or baseline changes."""
    conn = db.init_db()
    failed = False
    try:
        queries = watches.queries(conn, watch_id)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None
    for q in queries:
        if not q.enabled:
            console.print(f"{q.site}: skipped (disabled)")
            continue
        try:
            matches = pipeline.dry_run(conn, q.id)
            console.print(f'{q.site}: {len(matches)} matches (query {q.id})')
        except Exception:
            # Raw transport errors can include private URLs or proxy credentials.
            console.print(f'{q.site}: fetch failed; no state changed')
            failed = True
    if failed:
        raise typer.Exit(1)
