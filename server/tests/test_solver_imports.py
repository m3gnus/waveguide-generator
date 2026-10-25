"""Regression coverage for solver/job modules imported in fresh interpreters."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORT_TIMEOUT_SECONDS = 60


def _import_in_fresh_interpreter(module: str) -> str:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=IMPORT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"{module}:\ntimed out after {IMPORT_TIMEOUT_SECONDS} s"
    if completed.returncode:
        return f"{module}:\n{completed.stdout}{completed.stderr}"
    return ""


@pytest.mark.serial
def test_solver_and_jobs_modules_import_independently() -> None:
    # Discover paths without importing packages (pkgutil.walk_packages imports
    # their __init__ modules). Each target must be the first server import in
    # its own process; clearing sys.modules cannot undo all import side effects.
    modules = sorted({
        ".".join(
            (path.parent if path.stem == "__init__" else path.with_suffix(""))
            .relative_to(REPO_ROOT).parts
        )
        for package in ("solver", "jobs")
        for path in (REPO_ROOT / "server" / package).rglob("*.py")
    })
    assert "server.solver.field_plane" in modules
    assert "server.jobs.runtime.core" in modules

    # A small bounded batch keeps the complete scan below a minute without
    # sharing interpreter state between any two targets.
    with ThreadPoolExecutor(max_workers=4) as pool:
        failures = [
            failure
            for failure in pool.map(_import_in_fresh_interpreter, modules)
            if failure
        ]

    assert not failures, "\n\n".join(failures)


def test_jobs_exports_remain_available_without_eager_assembly() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", """
import sys
import server.jobs as jobs
from server.jobs.models import FieldPlaneRequest
from server.jobs.store import JobStore as StoredJobStore

assert 'server.jobs.api' not in sys.modules
assert 'server.jobs.runtime' not in sys.modules
assert set(jobs.__all__) == {'JobRuntime', 'JobStore', 'mount_jobs'}
assert set(jobs.__all__) <= set(dir(jobs))

from server.jobs import JobRuntime, JobStore, mount_jobs
from server.jobs.runtime import JobRuntime as RuntimeJobRuntime
from server.jobs.api import mount_jobs as api_mount_jobs

assert JobStore is StoredJobStore
assert JobRuntime is RuntimeJobRuntime
assert mount_jobs is api_mount_jobs
"""],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
