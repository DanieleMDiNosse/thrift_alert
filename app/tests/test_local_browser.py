from contextlib import contextmanager
from types import SimpleNamespace
from subitoo.config import Settings
from subitoo.core.fetch import BrowserClient, build_context


def test_local_browser_has_no_remote_health_requirement():
    c = build_context(Settings(browser_backend='local'), needs_browser=True)
    assert isinstance(c.browser, BrowserClient)


def test_doctor_reports_local_dependency_not_remote_connection(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from subitoo.cli import app
    import subitoo.config as config
    monkeypatch.setattr(config, "_settings", Settings(browser_backend="local", db_path=str(tmp_path/"state.db")))
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "local browser dependency" in result.output
    assert "browser reachable at ws" not in result.output


def test_local_session_uses_unmodified_firefox(monkeypatch):
    import sys
    events = []
    class Page:
        def on(self, *args): pass
    class Context:
        def new_page(self): return Page()
        def close(self): events.append('context_closed')
    class Browser:
        def new_context(self, **kwargs): return Context()
        def close(self): events.append('browser_closed')
    class Firefox:
        def launch(self, **kwargs):
            assert kwargs == {'headless': True, 'firefox_user_prefs': {'devtools.jsonview.enabled': False}}
            events.append('launch')
            return Browser()
    @contextmanager
    def playwright():
        yield SimpleNamespace(firefox=Firefox())
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=playwright))
    with BrowserClient(Settings(browser_backend='local')).session():
        pass
    assert events == ['launch', 'context_closed', 'browser_closed']
