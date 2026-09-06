"""A fresh server test run refuses missing frontend assets once, before tests."""

from pathlib import Path
import os
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("built", [False, True])
def test_server_suite_requires_frontend_before_running_tests(tmp_path, built):
    tests = tmp_path / "server" / "tests"
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_text(
        (ROOT / "server" / "tests" / "conftest.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    marker = tmp_path / "test-ran"
    (tests / "test_example.py").write_text(
        "from pathlib import Path\n"
        f"def test_example():\n    Path({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    if built:
        index = tmp_path / "frontend" / "dist" / "index.html"
        index.parent.mkdir(parents=True)
        index.write_text("<!doctype html><title>fixture</title>", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests), "-q"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    output = completed.stdout + completed.stderr
    if built:
        assert completed.returncode == 0, output
        assert marker.is_file()
    else:
        assert completed.returncode == pytest.ExitCode.USAGE_ERROR, output
        assert not marker.exists()
        assert "npm --prefix frontend ci" in output
        assert "npm --prefix frontend run build" in output
        assert "Traceback" not in output
