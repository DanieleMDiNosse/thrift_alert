import os
import subprocess
from pathlib import Path


def test_wrapper_new_database_is_owner_only(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path/"private"/"state.db"
    env = {**os.environ, "DB_PATH": str(path)}
    result = subprocess.run([str(root/"marketwatch"), "db", "init"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert path.stat().st_mode & 0o077 == 0
    assert path.parent.stat().st_mode & 0o077 == 0


def test_local_wrapper_works_from_another_directory(tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([str(root/'marketwatch'), '--help'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'watch' in result.stdout and 'serve' in result.stdout
