"""Only server tests that mount the app need the built frontend, and say so clearly."""

from pathlib import Path
import os
import subprocess
import sys



ROOT = Path(__file__).resolve().parents[2]

_TESTS = '''
from pathlib import Path
from starlette.staticfiles import StaticFiles

def test_pure():
    Path({marker!r}).touch()

def test_mounts_the_app():
    # what server.app.create_app does with the built SPA
    StaticFiles(directory=Path({dist!r}), html=True)

def test_unrelated_runtime_error():
    raise RuntimeError("something else entirely")
'''


def _run(tmp_path, built):
    tests = tmp_path / "server" / "tests"
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_text(
        (ROOT / "server" / "tests" / "conftest.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    marker = tmp_path / "test-ran"
    dist = tmp_path / "frontend" / "dist"
    (tests / "test_example.py").write_text(
        _TESTS.format(marker=str(marker), dist=str(dist)), encoding="utf-8"
    )
    if built:
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests), "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    return completed.stdout + completed.stderr, marker


def test_without_dist_only_the_app_mounting_test_is_refused(tmp_path):
    output, marker = _run(tmp_path, built=False)
    assert marker.is_file(), "a pure test must not need frontend/dist"
    assert "2 failed, 1 passed" in output, output
    assert "npm --prefix frontend ci" in output
    assert "npm --prefix frontend run build" in output
    # exactly one failure carries the build instruction; the unrelated error keeps its own
    assert output.count("npm --prefix frontend run build") == 1, output
    assert "something else entirely" in output


def test_with_dist_the_app_mounting_test_passes(tmp_path):
    output, marker = _run(tmp_path, built=True)
    assert marker.is_file()
    assert "1 failed, 2 passed" in output, output
    assert "npm --prefix frontend" not in output
