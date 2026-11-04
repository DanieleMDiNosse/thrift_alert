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


def _drain_orphan_ai_jobs(conn, stop) -> None:
    """Fallback supervisor: if queued worth/review jobs exist while no standalone
    AI worker holds the per-DB lock, drain up to 3 jobs during the heartbeat."""
    try:
        if not conn.execute(
            "SELECT 1 FROM ai_jobs WHERE status='queued' AND kind='worth' LIMIT 1"
        ).fetchone():
            return
        db_row = conn.execute("PRAGMA database_list").fetchone()
        db_file = db_row[2] if db_row else None
        if not db_file or db_file == ":memory:":
            return
        lock_path = Path(str(Path(db_file).expanduser().resolve()) + ".ai-worker.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            try:
                from subitoo.ai.config import get_config
                from subitoo.ai.worker import Worker

                worker = Worker(conn, get_config())
                worker.recover_stale()
                for _ in range(3):
                    if stop.is_set() or not worker.run_once():
                        break
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except Exception:
        log.debug("orphan AI job check skipped", exc_info=True)


def serve(conn, *, lock_path: Path, interval: int = 60, stop=None, tick=run_due):
    if interval < 60:
        raise ValueError('heartbeat interval must be at least 60 seconds')
    stop = stop if stop is not None else threading.Event()
    try:
        with dispatcher_lock(lock_path):
            log.info('dispatcher ready; heartbeat=%ss', interval)
            while not stop.is_set():
                if tick is run_due:
                    tick(conn, should_stop=stop.is_set)
                    _drain_orphan_ai_jobs(conn, stop)
                else:
                    tick(conn)
                stop.wait(interval)
    finally:
        conn.close()
