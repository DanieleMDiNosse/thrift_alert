from types import SimpleNamespace
import pytest
from typer.testing import CliRunner
from subitoo.cli import app


def test_dispatcher_loop_reuses_pipeline_and_closes_connection(tmp_path):
    from subitoo.core.scheduler import serve
    calls = []
    class Stop:
        def is_set(self): return len(calls) == 2
        def wait(self, seconds): assert seconds == 60
    class Conn:
        def close(self): calls.append('closed')
    def tick(c): calls.append('tick')
    serve(Conn(), lock_path=tmp_path/'lock', stop=Stop(), tick=tick)
    assert calls == ['tick', 'tick', 'closed']


def test_second_dispatcher_is_rejected(tmp_path):
    from subitoo.core.scheduler import dispatcher_lock
    with dispatcher_lock(tmp_path/'lock'):
        with pytest.raises(RuntimeError, match='already running'):
            with dispatcher_lock(tmp_path/'lock'):
                pass


def test_serve_rejects_aggressive_interval():
    result = CliRunner().invoke(app, ['serve', '--interval', '1'])
    assert result.exit_code != 0
    assert '60' in result.output
