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

from server.platform.warmup import BackgroundWarmup


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


async def _open_and_close_a_session() -> None:
    await run_on_gmsh_worker(lambda: None)


#: The process-wide session warmup. One gmsh library per process, so one warmup.
gmsh_warmup = BackgroundWarmup("gmsh-session", _open_and_close_a_session)


async def prewarm_gmsh_worker() -> None:
    """Start the owner thread and verify that a worker-owned session can open.

    This awaited ``run_on_gmsh_worker`` directly until it was measured: Uvicorn
    runs the whole lifespan startup before it calls ``loop.create_server``, so
    anything awaited in a startup handler delays the listen socket rather than
    merely delaying itself. ``import gmsh`` alone is 283-350 ms in a cold
    process and loads a large native library, which is worse on the launch that
    matters most -- the first one after a reboot, with a cold file cache and
    real-time antivirus watching. ``server/platform/warmup.py`` states the
    policy the other two prewarms already follow; this one now follows it too.

    The name is deliberately unchanged: ``test_create_app_registers_every_prewarm``
    pins the handler names registered on the router.
    """

    await gmsh_warmup.start()


async def shutdown_gmsh_worker() -> None:
    """Finalize the executor without moving gmsh work onto another thread."""

    global _executor, _shutting_down

    # Drain first. The warmup owns a queued executor future, and tearing the
    # executor down underneath it would abandon a task that is about to touch a
    # native session. Draining is what makes "job tasks stop, then the gmsh
    # owner finalizes" true for the warmup as well as for solves.
    await gmsh_warmup.stop()

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
    "gmsh_warmup",
    "prewarm_gmsh_worker",
    "run_on_gmsh_worker",
    "shutdown_gmsh_worker",
]
