"""Regression coverage for solver modules imported in fresh interpreters."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "server.solver.context",
        "server.solver.recombine",
        "server.solver.result_mapping",
        "server.solver.frequency_sweep",
    ],
)
def test_solver_module_imports_independently(module: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
