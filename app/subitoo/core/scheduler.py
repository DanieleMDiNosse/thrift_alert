"""Socket-free local heartbeat: scheduling remains in pipeline.run_due.

Use one dispatcher, not this together with Ofelia/cron. Errors remain latched for
explicit review/retry rather than repeatedly requesting blocked marketplaces.
"""
from contextlib import contextmanager
import fcntl
import logging
from pathlib import Path
import threading

from subitoo.core.pipeline import run_due

log = logging.getLogger('subitoo.scheduler')


@contextmanager
def dispatcher_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('dispatcher already running for this database') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def serve(conn, *, lock_path: Path, interval: int = 60, stop=None, tick=run_due):
    if interval < 60:
        raise ValueError('heartbeat interval must be at least 60 seconds')
    stop = stop if stop is not None else threading.Event()
    try:
        with dispatcher_lock(lock_path):
            log.info('dispatcher ready; heartbeat=%ss', interval)
            while not stop.is_set():
                tick(conn)
                stop.wait(interval)
    finally:
        conn.close()
