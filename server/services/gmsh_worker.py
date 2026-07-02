"""Dedicated worker thread for gmsh-backed mesh builds.

A gmsh build runs for multiple seconds; executed inline in a request handler
or job coroutine it freezes the event loop, stalling every HTTP response
(status polling, job list, viewport) until the build finishes. It also cannot
be pushed onto arbitrary pool threads (``asyncio.to_thread``): the gmsh
Python API drives one global session, and ``gmsh.initialize()`` defaults to
installing a SIGINT handler, which Python permits only on the main thread.
All gmsh work in the server therefore funnels through this module: a single
persistent worker thread executes submitted builds one at a time, in
submission order, inside a session it opens itself (``interruptible=False``,
so no signal handler is needed), while the event loop awaits the results
without blocking.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import threading
from typing import Any, Callable, TypeVar

GMSH_WORKER_THREAD_NAME = "gmsh-worker"

_executor: concurrent.futures.ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()

T = TypeVar("T")


def _gmsh_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the lazily created single-thread executor that owns gmsh."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=GMSH_WORKER_THREAD_NAME,
            )
        return _executor


def _run_in_gmsh_session(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Execute *fn* on the worker thread inside a worker-owned gmsh session.

    Builders (hornlab_mesher, the STEP writer) reuse an already-initialized
    session and only call ``gmsh.initialize()`` themselves when none exists —
    with default arguments, which install a SIGINT handler and therefore fail
    off the main thread. Opening the session here with ``interruptible=False``
    keeps their initialize path dormant. The session is closed again when the
    job ends so no cross-thread session leaks to code that runs gmsh directly
    on its own thread (tests, scripts).
    """
    try:
        import gmsh
    except Exception:
        # No gmsh runtime: run the job anyway and let the builder raise its
        # own descriptive error (call sites gate on runtime readiness).
        gmsh = None

    opened_here = False
    if gmsh is not None and not gmsh.isInitialized():
        gmsh.initialize(interruptible=False)
        opened_here = True
    try:
        return fn(*args, **kwargs)
    finally:
        if opened_here and gmsh.isInitialized():
            gmsh.finalize()


async def run_on_gmsh_worker(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run *fn* on the gmsh worker thread and await its result.

    Concurrent callers queue up: builds execute strictly one at a time and
    always on the same thread, so gmsh never sees interleaved or cross-thread
    calls. Exceptions raised by *fn* (including cancellation exceptions from
    job callbacks) propagate to the awaiting coroutine unchanged.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _gmsh_executor(), functools.partial(_run_in_gmsh_session, fn, *args, **kwargs)
    )
