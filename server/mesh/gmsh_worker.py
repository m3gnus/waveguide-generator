"""The single persistent worker thread that owns every gmsh call.

This ports v1 ``server/services/gmsh_worker.py:1-84``.  In particular, gmsh is
initialized on the worker with ``interruptible=False``: its default SIGINT
handler may only be installed from Python's main thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from contextlib import contextmanager
import functools
import logging
import os
from pathlib import Path
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

from server.platform.shutdown_backstop import shutdown_wait_limit
from server.platform.warmup import BackgroundWarmup
from server.platform.signal_rearm import (
    rearm_registered_signals,
    signal_rearm_is_needed,
)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetEnvironmentVariableW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    _kernel32.GetEnvironmentVariableW.restype = wintypes.DWORD
    _kernel32.SetEnvironmentVariableW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    _kernel32.SetEnvironmentVariableW.restype = wintypes.BOOL


log = logging.getLogger("wg.mesh")

GMSH_WORKER_THREAD_NAME = "gmsh-worker"
#: Test-only switch read by ``_park_for_test``.
BLOCK_FOR_TEST_ENV = "WG2_TEST_GMSH_BLOCK_FILE"
T = TypeVar("T")

_executor: concurrent.futures.ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()
_executor_condition = threading.Condition(_executor_lock)
_shutting_down = False
#: Operations submitted and not yet settled (queued or running), and whether
#: the last stop with a deadline stopped waiting for one of them
#: (``gmsh_call_abandoned``).
_calls_in_flight = 0
_in_flight_condition = threading.Condition()
_abandoned_call = False

_ERROR_ENVVAR_NOT_FOUND = 203


def _read_native_windows_path() -> str | None:
    """Read PATH from Win32, whose value can diverge from ``os.environ``."""

    ctypes.set_last_error(0)
    size = _kernel32.GetEnvironmentVariableW("PATH", None, 0)
    if size == 0:
        error = ctypes.get_last_error()
        if error in (0, _ERROR_ENVVAR_NOT_FOUND):
            return None
        raise ctypes.WinError(error)

    while True:
        buffer = ctypes.create_unicode_buffer(size)
        ctypes.set_last_error(0)
        length = _kernel32.GetEnvironmentVariableW("PATH", buffer, size)
        if length == 0:
            error = ctypes.get_last_error()
            if error in (0, _ERROR_ENVVAR_NOT_FOUND):
                return None
            raise ctypes.WinError(error)
        if length < size:
            return buffer.value
        size = length


@contextmanager
def _preserve_native_windows_path() -> Iterator[None]:
    """Undo native PATH mutations made by a Windows Gmsh API call."""

    if sys.platform != "win32":
        yield
        return

    path = _read_native_windows_path()
    try:
        yield
    finally:
        if not _kernel32.SetEnvironmentVariableW("PATH", path):
            failure = ctypes.WinError(ctypes.get_last_error())
            # Raising from here would replace whatever the body was already
            # raising, and the body's exception is the one that explains a
            # failed mesh or export. Report a damaged PATH on its own only
            # when the body succeeded and there is nothing to mask.
            if sys.exc_info()[1] is None:
                raise failure
            log.error("Could not restore the native PATH after a Gmsh call: %s", failure)


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


def _rearm_signals_on_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Undo native signal changes before Gmsh work continues."""

    if not signal_rearm_is_needed():
        return
    completed = threading.Event()

    def rearm() -> None:
        try:
            rearm_registered_signals()
        finally:
            completed.set()

    try:
        loop.call_soon_threadsafe(rearm)
    except RuntimeError:
        return
    # The event loop is live whenever this function is reached through the
    # public async worker. Bound the handshake anyway so shutdown cannot wedge
    # if a caller tears an unusual loop down underneath an in-flight request.
    completed.wait(timeout=1.0)


def _run_in_gmsh_session(
    fn: Callable[..., T],
    /,
    *args: Any,
    _signal_loop: asyncio.AbstractEventLoop | None = None,
    **kwargs: Any,
) -> T:
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
        # Gmsh 4.15.2 truncates the native Windows PATH while leaving Python's
        # cached os.environ value untouched. Restore the actual process value
        # immediately so callbacks and later bare-name subprocesses keep
        # resolving. POSIX enters the same no-op context without environment
        # access.
        with _preserve_native_windows_path():
            gmsh.initialize(interruptible=False)
        opened_here = True
        if _signal_loop is not None:
            _rearm_signals_on_loop(_signal_loop)
    try:
        _park_for_test(fn)
        return fn(*args, **kwargs)
    finally:
        if opened_here and gmsh is not None and gmsh.isInitialized():
            with _preserve_native_windows_path():
                gmsh.finalize()
            if _signal_loop is not None:
                _rearm_signals_on_loop(_signal_loop)


def _call_settled(_future: object = None) -> None:
    global _calls_in_flight
    with _in_flight_condition:
        _calls_in_flight -= 1
        _in_flight_condition.notify_all()


def _submit_counted(
    executor: concurrent.futures.ThreadPoolExecutor, call: Callable[[], T]
) -> concurrent.futures.Future[T]:
    """Submit one operation, counted from submission until its future settles.

    Counting from submission, rather than from when the thread picks the call
    up, leaves no moment in which a call is about to start but is not yet
    counted, so an idle answer from ``_wait_until_idle`` is always true.
    """

    global _calls_in_flight
    with _in_flight_condition:
        _calls_in_flight += 1
    try:
        future = executor.submit(call)
    except BaseException:
        _call_settled()
        raise
    future.add_done_callback(_call_settled)
    return future


def _wait_until_idle(timeout: float) -> bool:
    with _in_flight_condition:
        return _in_flight_condition.wait_for(lambda: _calls_in_flight == 0, timeout)


def gmsh_call_abandoned() -> bool:
    """Whether shutdown stopped waiting for a gmsh call that is still running.

    Such a call holds interpreter exit, which joins executor threads, so
    ``launch/serve.py`` asks this once its cleanup is done and ends the process
    instead of waiting out the rest of its budget.
    """

    with _in_flight_condition:
        return _abandoned_call and _calls_in_flight > 0


async def run_on_gmsh_worker(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Queue a gmsh operation on the persistent owner thread.

    Concurrent submissions stay serialized and exceptions propagate unchanged,
    matching v1 ``server/services/gmsh_worker.py:73-84``.
    """

    loop = asyncio.get_running_loop()
    # What ``loop.run_in_executor`` does, with the submission counted.
    future = _submit_counted(
        _gmsh_executor(),
        functools.partial(
            _run_in_gmsh_session,
            fn,
            *args,
            _signal_loop=loop,
            **kwargs,
        ),
    )
    return await asyncio.wrap_future(future, loop=loop)


def _no_gmsh_work() -> None:
    """The warmup's operation: a session is opened and closed around nothing."""


def _park_for_test(fn: Callable[..., Any]) -> None:
    """Stand in for an OCC call that never returns, when a test asks for one.

    Test-only, and inert unless ``WG2_TEST_GMSH_BLOCK_FILE`` names a file.
    The shutdown harness (``server/tests/test_bounded_server_shutdown.py``)
    has to hold this thread in a call Python cannot interrupt, because that is
    what an OCC build does and it is what keeps a process alive: interpreter
    exit joins executor threads. An uninterruptible ``threading.Event().wait()``
    has exactly that property. The file records which operation was parked, so
    the harness knows the build it queued is the one in flight. Nothing in the
    application sets the variable, and the session warmup is never parked.
    """

    marker = os.environ.get(BLOCK_FOR_TEST_ENV)
    if not marker or fn is _no_gmsh_work:
        return
    staged = Path(f"{marker}.tmp")
    staged.write_text(f"{getattr(fn, '__qualname__', repr(fn))}\n", encoding="utf-8")
    staged.replace(marker)
    threading.Event().wait()


async def _open_and_close_a_session() -> None:
    await run_on_gmsh_worker(_no_gmsh_work)


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
    """Finalize the executor without moving gmsh work onto another thread.

    With no stop budget running (tests, the CLI, an embedder) this drains:
    queued and running work finishes, then the thread is joined. Once
    ``launch/serve.py`` has begun a stop budget it cannot drain an OCC build,
    which has no cancellation point and may outlive the whole budget. So queued
    work is dropped, a running call is waited for only while
    ``shutdown_wait_limit`` allows, and past that the join is skipped and the
    call is recorded as abandoned (``gmsh_call_abandoned``).
    """

    global _executor, _shutting_down, _abandoned_call

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
            _abandoned_call = False
            executor = _executor
    if wait_for_existing:
        await asyncio.to_thread(wait_for_other_shutdown)
        return
    try:
        if executor is not None:
            limit = shutdown_wait_limit(None)
            if limit is None:
                await asyncio.to_thread(executor.shutdown, True, cancel_futures=False)
            else:
                executor.shutdown(wait=False, cancel_futures=True)
                if await asyncio.to_thread(_wait_until_idle, limit):
                    await asyncio.to_thread(executor.shutdown, True)
                else:
                    with _in_flight_condition:
                        _abandoned_call = True
                    log.warning(
                        "A gmsh call was still running when shutdown ran out of time "
                        "for it; not waiting for it"
                    )
    finally:
        with _executor_condition:
            if _executor is executor:
                _executor = None
            _shutting_down = False
            _executor_condition.notify_all()


__all__ = [
    "GMSH_WORKER_THREAD_NAME",
    "gmsh_call_abandoned",
    "gmsh_warmup",
    "prewarm_gmsh_worker",
    "run_on_gmsh_worker",
    "shutdown_gmsh_worker",
]
