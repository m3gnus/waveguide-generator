"""Exercise the real suite isolation and grouping hooks in two worker processes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _isolated_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_TESTRUNUID"):
        environment.pop(name, None)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    return environment


def test_a_crashed_worker_ends_the_run_instead_of_idling_it(tmp_path):
    """loadgroup never finishes after replacing a crashed worker; restarts stay off.

    Without the suite's default the inner run below never returns: the
    surviving worker keeps its last test waiting for work, and the timeout here
    is what a CI job limit was on 2026-10-25.
    """

    pytest.importorskip("xdist")
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text((REPO_ROOT / "pytest.ini").read_text())
    (suite / "conftest.py").write_text((REPO_ROOT / "conftest.py").read_text())
    (suite / "test_crash.py").write_text('''
import os
import pytest

@pytest.mark.parametrize("number", range(6))
def test_before(number):
    pass

def test_crash():
    os._exit(1)

@pytest.mark.parametrize("number", range(6))
def test_after(number):
    pass
''')
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(suite), "-n2", "--dist=loadgroup",
         "-q", "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'inner-basetemp'}"],
        cwd=suite, env=_isolated_environment(), capture_output=True, text=True, timeout=60,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    assert "crashed while running 'test_crash.py::test_crash'" in output
    assert "1 failed" in output


def test_workers_have_private_data_and_serial_marks_stay_on_one_worker(tmp_path):
    pytest.importorskip("xdist")
    suite = tmp_path / "suite"
    suite.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (suite / "pytest.ini").write_text((REPO_ROOT / "pytest.ini").read_text())
    (suite / "conftest.py").write_text(
        (REPO_ROOT / "conftest.py").read_text() + '''

import json
def pytest_sessionstart(session):
    worker = getattr(session.config, "workerinput", {}).get("workerid", "controller")
    output = Path(os.environ["TEST_ISOLATION_EVIDENCE"]) / (worker + ".json")
    output.write_text(json.dumps({"pid": os.getpid(), "data": str(SANDBOX_DATA_DIR)}))

def pytest_collection_finish(session):
    worker = getattr(session.config, "workerinput", {}).get("workerid", "controller")
    output = Path(os.environ["TEST_ISOLATION_EVIDENCE"]) / (worker + "-collection.json")
    output.write_text(json.dumps([item.nodeid for item in session.items]))
'''
    )
    (suite / "test_probe.py").write_text('''
import json
import os
from pathlib import Path
import pytest
from server.platform.paths import data_paths

@pytest.mark.parametrize("number", range(8))
def test_private_data(number, sandbox_data_dir):
    assert data_paths().root == sandbox_data_dir
    assert os.environ["OPENBLAS_NUM_THREADS"] == "1"

@pytest.mark.serial
@pytest.mark.parametrize("number", range(4))
def test_group(number):
    output = Path(os.environ["TEST_ISOLATION_EVIDENCE"]) / ("group-" + str(number) + ".json")
    output.write_text(json.dumps(os.getpid()))
''')
    environment = _isolated_environment()
    environment["TEST_ISOLATION_EVIDENCE"] = str(evidence)
    # The children's tmp_path lives under ours, so the outer --basetemp governs
    # every run and none touches the user-wide pytest-of-<user> root.
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(suite), "-n2", "--dist=loadgroup",
         "-q", "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'inner-basetemp'}"],
        cwd=suite, env=environment, capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "12 passed" in completed.stdout
    workers = [json.loads((evidence / f"{name}.json").read_text()) for name in ("controller", "gw0", "gw1")]
    assert len({worker["pid"] for worker in workers}) == 3
    assert len({worker["data"] for worker in workers}) == 3
    assert all(not Path(worker["data"]).exists() for worker in workers)
    for name in ("gw0", "gw1"):
        collection = json.loads((evidence / f"{name}-collection.json").read_text())
        grouped = [node for node in collection if "::test_group[" in node]
        assert len(grouped) == 4 and all(node.endswith("@serial") for node in grouped)
    assert len({json.loads(path.read_text()) for path in evidence.glob("group-*.json")}) == 1
