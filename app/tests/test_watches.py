"""Watch groups project to ordinary queries without fetching during configuration."""
import json
import pytest
from typer.testing import CliRunner
from subitoo.cli import app
from subitoo.core import db, pipeline
from tests._helpers import FakeSite, make_listing


def definition(name='camera'):
    return {'name': name, 'title_include': ['a6400'], 'title_exclude': ['broken'],
            'max_price_eur': 550, 'sources': {'fake': {'search_url': 'https://example.com/search'}}}


def test_watch_crud_and_validation(conn):
    from subitoo.core import watches
    spec = definition()
    wid = watches.save(conn, spec)
    assert watches.get(conn, wid)['name'] == 'camera'
    queries = watches.queries(conn, wid)
    assert len(queries) == 1 and queries[0].cron == '*/30 * * * *'
    assert not queries[0].notify_on_price_change
    qid = queries[0].id
    watches.save(conn, {**spec, 'enabled': False}, watch_id=wid)
    assert not db.get_query(conn, qid).enabled
    watches.save(conn, spec, watch_id=wid)
    assert db.get_query(conn, qid).enabled
    assert len(watches.list_all(conn)) == 1
    watches.remove(conn, wid)
    assert db.get_query(conn, qid) is None
    with pytest.raises(ValueError):
        watches.save(conn, {**spec, 'max_price_eur': -1})
    with pytest.raises(ValueError):
        watches.save(conn, {**spec, 'unknown_setting': True})
    assert watches.list_all(conn) == []


def test_source_disable_preserves_history(conn, recorder):
    from subitoo.core import watches
    spec = definition()
    wid = watches.save(conn, spec)
    qid = watches.queries(conn, wid)[0].id
    FakeSite.to_return = [make_listing("old", title="a6400")]
    pipeline.run_query(conn, qid, send=recorder)
    spec["sources"]["fake"]["enabled"] = False
    watches.save(conn, spec, watch_id=wid)
    assert db.get_query(conn, qid) is not None
    assert not db.get_query(conn, qid).enabled
    spec["sources"]["fake"]["enabled"] = True
    watches.save(conn, spec, watch_id=wid)
    assert watches.queries(conn, wid)[0].id == qid
    assert db.get_query(conn, qid).seeded


def test_watch_edit_reseeds_only_changed_source(conn, recorder):
    from subitoo.core import watches
    spec = definition()
    wid = watches.save(conn, spec)
    qid = watches.queries(conn, wid)[0].id
    FakeSite.to_return = [make_listing('old', title='sony a6400')]
    pipeline.run_query(conn, qid, send=recorder, sleep=lambda _: None)
    watches.save(conn, {**spec, 'max_price_eur': 600}, watch_id=wid)
    new = watches.queries(conn, wid)[0]
    assert not new.seeded
    FakeSite.to_return += [make_listing('also-old', title='sony a6400', price=590)]
    pipeline.run_query(conn, new.id, send=recorder, sleep=lambda _: None)
    assert recorder.sent == []


def test_watch_literal_filters_and_shared_group_notification(conn, recorder):
    from subitoo.core import watches
    one = watches.save(conn, definition('camera'))
    two = watches.save(conn, definition('other'))
    for wid in [one, two]:
        FakeSite.to_return = []
        pipeline.run_query(conn, watches.queries(conn, wid)[0].id, send=recorder)
    FakeSite.to_return = [make_listing('new', title='Sony A6400', price=500),
                          make_listing('bad', title='broken a6400'),
                          make_listing('expensive', title='a6400', price=600)]
    for wid in [one, two]:
        pipeline.run_query(conn, watches.queries(conn, wid)[0].id, send=recorder, sleep=lambda _: None)
    assert len(recorder.sent) == 1
    assert recorder.sent[0].query_name == 'camera'


def test_watch_euro_bounds_do_not_compare_foreign_currency(conn, recorder):
    from subitoo.core import watches
    wid = watches.save(conn, definition())
    qid = watches.queries(conn, wid)[0].id
    pipeline.run_query(conn, qid, send=recorder)
    foreign = make_listing("foreign", title="a6400", price=10)
    foreign.currency = "GBP"
    FakeSite.to_return = [foreign]
    assert pipeline.dry_run(conn, qid) == []
    pipeline.run_query(conn, qid, send=recorder, sleep=lambda _: None)
    assert recorder.sent == []


def test_disabled_facebook_does_not_require_adapter(conn):
    from subitoo.core import watches
    spec = definition()
    spec['sources']['facebook'] = {'enabled': False, 'search_url': '<paste URL>'}
    wid = watches.save(conn, spec)
    assert len(watches.queries(conn, wid)) == 1
    spec['sources']['facebook']['enabled'] = True
    with pytest.raises(ValueError, match='facebook'):
        watches.save(conn, spec)


@pytest.mark.parametrize("disable_group", [True, False])
def test_watch_test_skips_disabled_sources(tmp_path, monkeypatch, disable_group):
    from subitoo.core import watches
    import subitoo.config as config
    monkeypatch.setattr(config, "_settings", config.Settings(db_path=str(tmp_path/"state.db")))
    c = db.init_db()
    spec = definition()
    wid = watches.save(c, spec)
    if disable_group:
        spec["enabled"] = False
    else:
        spec["sources"]["fake"]["enabled"] = False
    watches.save(c, spec, watch_id=wid)
    def must_not_fetch(*args):
        raise AssertionError("disabled source contacted")
    monkeypatch.setattr(pipeline, "dry_run", must_not_fetch)
    result = CliRunner().invoke(app, ["watch", "test", str(wid)])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output.lower()


def test_watch_cli_roundtrip(tmp_path, monkeypatch):
    import subitoo.config as config
    monkeypatch.setattr(config, '_settings', config.Settings(db_path=str(tmp_path/'db.sqlite')))
    f = tmp_path/'watch.json'
    f.write_text(json.dumps(definition()))
    runner = CliRunner()
    for args in [['watch','add','--from-json',str(f)], ['watch','list'],
                 ['watch','show','1'], ['watch','disable','1'], ['watch','enable','1'],
                 ['watch','edit','1','--from-json',str(f)], ['watch','rm','1','--yes']]:
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
