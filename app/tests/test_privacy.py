"""Diagnostics must not retain configured secrets or credentials in URLs."""
from subitoo.core import db, pipeline
from tests.test_pipeline import mkquery
from tests._helpers import FakeSite


def test_pipeline_redacts_transport_error(conn, recorder, monkeypatch, caplog):
    secret = 'private-fixture-topic-123456'
    monkeypatch.setenv('NTFY_TOPIC', secret)
    def fail(*args):
        raise RuntimeError('HTTP 403 '+secret+' https://user:password@example.com/path')
    monkeypatch.setattr(FakeSite, 'fetch', fail)
    qid = mkquery(conn)
    pipeline.run_query(conn, qid, send=recorder)
    diagnostic = db.recent_runs(conn, qid)[0]['error_message']
    assert secret not in diagnostic and 'password' not in diagnostic
    assert secret not in caplog.text and 'password' not in caplog.text
    assert '403' in diagnostic


def test_http_timeout_is_bounded():
    from subitoo.core.fetch import HttpClient
    from subitoo.config import Settings
    class Session:
        def get(self, url, **kwargs): return kwargs
    client = HttpClient(Settings())
    client._session = Session()
    assert client.get('https://example.com')['timeout'] == 30
    assert client.get('https://example.com', timeout=20)['timeout'] == 20
