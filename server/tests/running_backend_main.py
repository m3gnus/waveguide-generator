"""Tests-only backend with private native exclusion and a slow interpreter exit."""

from __future__ import annotations

import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import sys
import time

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from server.platform import running_exclusion  # noqa: E402
from server.solver.beat_runtime import retire  # noqa: E402
from server.tests.beat_runtime.fake_host_main import _test_retire_event_name  # noqa: E402


def _test_running_mutex_name(name: str) -> str:
    """Refuse production or namespaced mutexes before any Windows API call."""
    prefix = "WaveguideGeneratorTestRunning-"
    if (not isinstance(name, str) or not name.startswith(prefix)
            or not name[len(prefix):].strip() or "\\" in name or "\0" in name):
        raise ValueError("Expected a private test running mutex name")
    return name


def _child(name: str, pipe: Connection) -> None:
    running_exclusion._hold(_test_running_mutex_name(name))
    try:
        pipe.send("ready")
        try:
            pipe.recv()
        except EOFError:
            return
        raise RuntimeError("Expected EOF from the backend")
    finally:
        pipe.close()


def main(argv: list[str] | None = None) -> int:
    # Validate both names before a child or serve.py can make any Win32 call.
    name = _test_running_mutex_name(os.environ.get("WG2_TEST_RUNNING_MUTEX", ""))
    event = _test_retire_event_name(os.environ.get("WG2_TEST_RETIRE_EVENT", ""))
    running_exclusion.RUNNING_MUTEX_NAME = name
    retire.RETIRE_IDLE_EVENT = event

    def hold() -> bool:
        running_exclusion._hold(name)
        return True

    running_exclusion.hold_running_exclusion = hold
    from launch import serve

    context = multiprocessing.get_context("spawn")
    parent_pipe, child_pipe = context.Pipe()
    child = context.Process(target=_child, args=(name, child_pipe))
    child.start()
    child_pipe.close()
    try:
        if not parent_pipe.poll(30) or parent_pipe.recv() != "ready":
            raise RuntimeError("Child did not announce its running mutex handle")
        pid_path = Path(os.environ["WG2_TEST_CHILD_PID_FILE"])
        temporary = pid_path.with_suffix(".tmp")
        temporary.write_text(f"{child.pid}\n", encoding="utf-8")
        temporary.replace(pid_path)
        code = serve.main(sys.argv[1:] if argv is None else argv)
    finally:
        parent_pipe.close()
        child.join(timeout=10)
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
            raise RuntimeError("Child did not exit after backend closed its pipe")
        if child.exitcode != 0:
            raise RuntimeError(f"Child exited with code {child.exitcode}")
        child.close()
    time.sleep(float(os.environ.get("WG2_TEST_EXIT_LINGER", "0")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
