"""Exercise the Bash launcher with isolated lightweight service processes."""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def stack(tmp_path):
    script = tmp_path / "start-local.sh"
    if not (ROOT / "start-local.sh").exists():
        pytest.fail("Missing start-local.sh launcher")
    shutil.copy2(ROOT / "start-local.sh", script)
    python = tmp_path / "app/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    package = tmp_path / "subitoo"
    (package / "ai").mkdir(parents=True)
    for path in [package / "__init__.py", package / "ai/__init__.py"]:
        path.write_text("")
    (package / "config.py").write_text(
        "from types import SimpleNamespace\ndef get_settings(): return SimpleNamespace(db_path='./data/test.sqlite')\n"
    )
    (package / "cli.py").write_text("""import sys,time,json
from http.server import BaseHTTPRequestHandler,HTTPServer
if sys.argv[1]=='gui':
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
        def log_message(self,*args): pass
    HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler).serve_forever()
else:
    while True: time.sleep(.1)
""")
    (package / "ai/worker.py").write_text("import time\nwhile True: time.sleep(.1)\n")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["MARKETWATCH_PORT"] = str(port)
    env["AI_ENDPOINT"] = "http://127.0.0.1:1/v1"

    def run(*args):
        return subprocess.run(
            ["bash", str(script), *args],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=25,
        )

    yield tmp_path, run, env
    run("stop")


def test_start_is_idempotent_status_stop_and_restart(stack):
    folder, run, _ = stack
    first = run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert "http://127.0.0.1:" in first.stdout
    paths = list((folder / "data/local-stack").glob("*.pid"))
    assert len(paths) == 3
    original = {p.name: p.read_text() for p in paths}
    assert run("start").returncode == 0
    assert {p.name: p.read_text() for p in paths} == original
    assert run("status").returncode == 0
    assert run("stop").returncode == 0
    assert not list((folder / "data/local-stack").glob("*.pid"))
    assert run("start").returncode == 0
    assert run("stop").returncode == 0


def test_foreign_stale_pid_is_not_signalled(stack):
    folder, run, _ = stack
    state = folder / "data/local-stack"
    state.mkdir(parents=True)
    (state / "monitor.pid").write_text(str(os.getpid()))
    assert run("stop").returncode == 0
    assert os.kill(os.getpid(), 0) is None


def test_invalid_action_and_port_do_not_start_services(stack):
    folder, run, env = stack
    assert run("invalid").returncode != 0
    bad = {**env, "MARKETWATCH_PORT": "not-a-port"}
    result = subprocess.run(
        ["bash", str(folder / "start-local.sh")],
        cwd=folder,
        env=bad,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not list((folder / "data/local-stack").glob("*.pid"))


def test_failed_start_rolls_back_only_new_services(stack):
    folder, run, _ = stack
    (folder / "subitoo/ai/worker.py").write_text("raise SystemExit(7)\n")
    result = run("start")
    assert result.returncode != 0
    assert "worker" in result.stderr
    assert not list((folder / "data/local-stack").glob("*.pid"))
    status = run("status")
    assert status.returncode == 1 and "attivo (PID" not in status.stdout
