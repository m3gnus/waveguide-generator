"""The single persistent worker thread that owns every gmsh call.

This ports v1 ``server/services/gmsh_worker.py:1-84``.  In particular, gmsh is
initialized on the worker with ``interruptible=False``: its default SIGINT
handler may only be installed from Python's main thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import threading
from collections.abc import Callable
from typing import Any, TypeVar


GMSH_WORKER_THREAD_NAME = "gmsh-worker"
T = TypeVar("T")

_executor: concurrent.futures.ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()
_executor_condition = threading.Condition(_executor_lock)
_shutting_down = False


def _gmsh_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Lazily create the one-thread FIFO executor (v1 ``gmsh_worker.py:32-41``)."""

    global _executor
    with _executor_lock:
        if _shutting_down:
            raise RuntimeError("gmsh worker is shutting down; submission rejected")
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=GMSH_WORKER_THREAD_NAME,
            )
        return _executor


def _run_in_gmsh_session(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Open and close a worker-owned gmsh session around one queued operation.

    Mesher builders reuse an existing session.  Opening it here suppresses their
    unsafe default initialization path; see v1 ``gmsh_worker.py:44-70``.
    """

    try:
        import gmsh
    except Exception:  # pragma: no cover - the builder gives the useful error
        gmsh = None

    opened_here = False
    if gmsh is not None and not gmsh.isInitialized():
        gmsh.initialize(interruptible=False)
        opened_here = True
    try:
        return fn(*args, **kwargs)
    finally:
        if opened_here and gmsh is not None and gmsh.isInitialized():
            gmsh.finalize()


async def run_on_gmsh_worker(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Queue a gmsh operation on the persistent owner thread.

    Concurrent submissions stay serialized and exceptions propagate unchanged,
    matching v1 ``server/services/gmsh_worker.py:73-84``.
    """

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _gmsh_executor(), functools.partial(_run_in_gmsh_session, fn, *args, **kwargs)
    )


async def prewarm_gmsh_worker() -> None:
    """Start the owner thread and verify that a worker-owned session can open."""

    await run_on_gmsh_worker(lambda: None)


async def shutdown_gmsh_worker() -> None:
    """Finalize the executor without moving gmsh work onto another thread."""

    global _executor, _shutting_down

    def wait_for_other_shutdown() -> None:
        with _executor_condition:
            while _shutting_down:
                _executor_condition.wait()

    with _executor_condition:
        if _shutting_down:
            wait_for_existing = True
            executor = None
        else:
            wait_for_existing = False
            _shutting_down = True
            executor = _executor
    if wait_for_existing:
        await asyncio.to_thread(wait_for_other_shutdown)
        return
    try:
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, True, cancel_futures=False)
    finally:
        with _executor_condition:
            if _executor is executor:
                _executor = None
            _shutting_down = False
            _executor_condition.notify_all()


__all__ = [
    "GMSH_WORKER_THREAD_NAME",
    "prewarm_gmsh_worker",
    "run_on_gmsh_worker",
    "shutdown_gmsh_worker",
]
