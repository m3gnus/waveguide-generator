"""The dense-solver memory override reaches the isolated CAD child.

Imported CAD geometry is meshed, and its dense-solver ceiling enforced, inside
the isolated child (``server/cadlink/isolated.py`` ->
``server/mesh/imported.py``). That child's environment is an allowlist, so
until the two memory variables were on it an operator's
``WG2_DENSE_SOLVER_MEMORY_LIMIT_BYTES`` held for parametric designs and was
silently ignored for imported ones.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from server.cadlink.isolation import child_environment
from server.mesh.builder import (
    DENSE_SOLVER_MEMORY_LIMIT_ENV,
    DENSE_SOLVER_MEMORY_LIMIT_UNSAFE_ENV,
)


def test_the_child_environment_carries_both_memory_variables(tmp_path: Path) -> None:
    source = {
        "PATH": "/usr/bin",
        "SYSTEMROOT": r"C:\Windows",
        DENSE_SOLVER_MEMORY_LIMIT_ENV: "123456789",
        DENSE_SOLVER_MEMORY_LIMIT_UNSAFE_ENV: "1",
        # Everything else in the application's namespace stays out.
        "WG2_DATA_DIR": "/data",
        "WG2_PORT": "8000",
    }
    for system in ("Darwin", "Linux", "Windows"):
        env = child_environment(tmp_path / "staging", environ=source, system=system)
        assert env[DENSE_SOLVER_MEMORY_LIMIT_ENV] == "123456789"
        assert env[DENSE_SOLVER_MEMORY_LIMIT_UNSAFE_ENV] == "1"
        assert "WG2_DATA_DIR" not in env
        assert "WG2_PORT" not in env


def test_unset_memory_variables_stay_unset(tmp_path: Path) -> None:
    env = child_environment(tmp_path / "staging", environ={"PATH": "/usr/bin"}, system="Linux")

    assert DENSE_SOLVER_MEMORY_LIMIT_ENV not in env
    assert DENSE_SOLVER_MEMORY_LIMIT_UNSAFE_ENV not in env


def test_a_child_with_that_environment_resolves_the_override(tmp_path: Path) -> None:
    """End to end through a real process started with the child's environment.

    The ceiling the child applies is whatever ``resolve_dense_solver_memory_limit``
    reads from its own environment, so this is the answer an imported mesh
    would be held to.
    """

    staging = tmp_path / "staging"
    (staging / "tmp").mkdir(parents=True)
    (staging / "home").mkdir()
    override = 64 * 1024**2
    import os

    environ = dict(os.environ)
    environ[DENSE_SOLVER_MEMORY_LIMIT_ENV] = str(override)
    env = child_environment(staging, environ=environ)
    probe = (
        "import json\n"
        "from server.mesh.builder import resolve_dense_solver_memory_limit\n"
        "limit = resolve_dense_solver_memory_limit()\n"
        "print(json.dumps({'bytes': limit.bytes, 'source': limit.source}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-s", "-c", probe],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    answer = json.loads(completed.stdout.strip().splitlines()[-1])
    assert answer == {"bytes": override, "source": "override"}
