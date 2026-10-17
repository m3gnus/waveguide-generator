"""Gmsh stays usable after the thread that first opened it has exited.

Gmsh 4.15.2 on macOS faults inside its bundled OpenMP runtime
(``__kmpc_critical_with_hint``) when the thread that first initialized Gmsh
has exited and another thread initializes it. Running
``test_mesh_child.py::test_in_process_switch_runs_on_the_gmsh_worker`` before
the ATH CORE preview test in one process crashed pytest with SIGSEGV/SIGBUS
that way. Each scenario runs in a fresh interpreter: the runtime's initial
thread is process-wide, and a regression would kill the test process.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("gmsh")

ROOT = Path(__file__).resolve().parents[2]


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_gmsh_opens_on_other_threads_after_the_worker_has_shut_down() -> None:
    result = _run(
        """
        import asyncio, threading
        import gmsh
        from server.mesh.gmsh_worker import run_on_gmsh_worker, shutdown_gmsh_worker

        async def first_session_on_the_worker():
            try:
                await run_on_gmsh_worker(lambda: None)
            finally:
                await shutdown_gmsh_worker()

        asyncio.run(first_session_on_the_worker())

        def session():
            gmsh.initialize(interruptible=False)
            gmsh.finalize()

        session()
        later = threading.Thread(target=session)
        later.start()
        later.join()
        asyncio.run(first_session_on_the_worker())
        print("ok")
        """
    )
    assert (result.returncode, result.stdout.strip()) == (0, "ok"), result.stderr


def test_pinning_from_a_worker_thread_parks_a_daemon_initial_thread() -> None:
    result = _run(
        """
        import threading
        import gmsh
        from server.platform.gmsh_openmp import PIN_THREAD_NAME, pin_gmsh_openmp_initial_thread

        def preview_worker():
            pin_gmsh_openmp_initial_thread()
            gmsh.initialize(interruptible=False)
            gmsh.finalize()

        worker = threading.Thread(target=preview_worker)
        worker.start()
        worker.join()
        roots = [t for t in threading.enumerate() if t.name == PIN_THREAD_NAME]
        assert len(roots) == 1 and roots[0].daemon and roots[0].is_alive(), roots
        pin_gmsh_openmp_initial_thread()
        assert [t.name for t in threading.enumerate()].count(PIN_THREAD_NAME) == 1
        gmsh.initialize(interruptible=False)
        gmsh.finalize()
        print("ok")
        """
    )
    assert (result.returncode, result.stdout.strip()) == (0, "ok"), result.stderr


def test_pinning_from_the_main_thread_needs_no_parked_thread() -> None:
    result = _run(
        """
        import threading
        import gmsh
        from server.platform.gmsh_openmp import PIN_THREAD_NAME, pin_gmsh_openmp_initial_thread

        pin_gmsh_openmp_initial_thread()
        assert PIN_THREAD_NAME not in [t.name for t in threading.enumerate()]

        def session():
            gmsh.initialize(interruptible=False)
            gmsh.finalize()

        for _ in range(2):
            worker = threading.Thread(target=session)
            worker.start()
            worker.join()
        session()
        print("ok")
        """
    )
    assert (result.returncode, result.stdout.strip()) == (0, "ok"), result.stderr
