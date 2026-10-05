"""Run OCC mesh builds in a killable child process.

The pinned mesher can abort or segfault inside ``gmsh``'s ``mesh.generate`` on
shapes the UI allows (an ICW design with a rectangle morph, a high fixed part
and shrinkage on), and some variants never return. A native abort on the gmsh
worker *thread* takes the whole server with it, and no pre-check can tell the
failing shapes apart from the working ones, so the build runs in a spawned
child instead:

* a crash (any signal or a nonzero exit) or a wall-clock timeout kills only the
  child and surfaces as :class:`MesherCrashError`, a named, user-facing error;
  the server stays up. Nothing is retried automatically (Magnus, 2026-10-05).
* one child stays warm across builds, so the 1-2 s ``gmsh``/OCC/scipy import
  is paid at boot (in the background) and again only after a death.
* cancelling a build kills the child, the only bounded way to stop OCC.

It follows the BEMPP worker's process model (``server/solver/bempp_process.py``):
the ``spawn`` context on every platform, a module-level target, a Windows job
object so the child dies with the server, and a parent-sentinel watchdog. The
packaged app is a relocatable interpreter rather than a frozen executable
(``docs/plans/STANDALONE-APP.md``), so there is no ``freeze_support`` concern:
``spawn`` re-runs ``python -m server`` under its ``__main__`` guard, as it does
for the BEMPP worker.

``WG2_MESH_IN_PROCESS=1`` runs builds on the in-process gmsh worker thread, the
pre-child behaviour. It exists for debugging and for tests that substitute the
mesher in ``sys.modules``, which a spawned child cannot see.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import multiprocessing
from multiprocessing.connection import Connection
import os
import pickle
import signal
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from server.platform.process_tree import confine_to_windows_job

log = logging.getLogger("wg.mesh")

IN_PROCESS_ENV = "WG2_MESH_IN_PROCESS"
TIMEOUT_ENV = "WG2_MESH_BUILD_TIMEOUT_S"
#: Above any legitimate build seen so far (a fine solve mesh is tens of seconds)
#: and well below the >240 s hangs the interpolating fit produces.
DEFAULT_TIMEOUT_SECONDS = 300.0

_POLL_SECONDS = 0.1
_JOIN_SECONDS = 2.0
_PARENT_GONE_EXIT_CODE = 3

#: The one remedy Magnus chose: say what happened and suggest the approximating
#: fit. There is no UI control for the surface fit today, so the setting is
#: named by the mesher's own value.
SURFACE_FIT_HINT = (
    "This is a known fault of the mesher's interpolating surface fit on some "
    'shapes. Try the approximating fit instead (surface fit "approximate"). '
    "The server is still running and nothing was retried."
)


class MesherCrashError(RuntimeError):
    """The mesher process died or ran out of time on this geometry."""


class MesherChildError(RuntimeError):
    """A failure inside the child that could not be sent back as itself."""


def child_enabled() -> bool:
    return os.environ.get(IN_PROCESS_ENV, "").strip() != "1"


def build_timeout_seconds() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _describe_exit(exitcode: int | None) -> str:
    if exitcode is None:
        return "it stopped responding"
    if exitcode < 0:
        try:
            name = signal.Signals(-exitcode).name
        except ValueError:
            name = f"signal {-exitcode}"
        return f"it was killed by {name}"
    if exitcode == 0:
        return "it exited without a result"
    return f"it exited with code {exitcode:#x}" if exitcode > 255 else f"it exited with code {exitcode}"


# ---------------------------------------------------------------- the child


def _exit_when_parent_does() -> None:
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def wait_and_exit() -> None:
        from multiprocessing.connection import wait

        wait([parent.sentinel])
        os._exit(_PARENT_GONE_EXIT_CODE)

    threading.Thread(target=wait_and_exit, name="wg2-mesh-parent-watch", daemon=True).start()


def _warm_this_process() -> None:
    """Pay the import and session-open cost here, in the process that reuses it."""

    from server.mesh.gmsh_worker import _no_gmsh_work, _run_in_gmsh_session
    from server.mesh.prewarm import import_mesher_modules

    import_mesher_modules()
    import meshio  # noqa: F401 - parsing a build's artifact needs it

    import server.mesh.builder  # noqa: F401
    import server.exports.core  # noqa: F401

    _run_in_gmsh_session(_no_gmsh_work)


def _error_payload(exc: BaseException) -> tuple[Any, ...]:
    blob: bytes | None
    try:
        blob = pickle.dumps(exc)
        pickle.loads(blob)  # an exception with a custom __init__ can pickle but not load
    except Exception:  # noqa: BLE001
        blob = None
    return ("error", blob, type(exc).__name__, str(exc), traceback.format_exc())


def _child_main(connection: Connection, session_root: str | None) -> None:
    """Serve builds one at a time. Spawn-safe: module level, picklable arguments."""

    from server.platform.temp_session import adopt_parent_session

    adopt_parent_session(session_root)
    _exit_when_parent_does()
    try:
        while True:
            try:
                command = connection.recv()
            except (EOFError, OSError):
                return
            except Exception as exc:  # noqa: BLE001 - unreadable command, keep serving
                connection.send(_error_payload(exc))
                continue
            if command is None:
                return
            kind = command[0]
            try:
                if kind == "warm":
                    _warm_this_process()
                    connection.send(("warm",))
                    continue
                from server.mesh.gmsh_worker import _run_in_gmsh_session

                _, fn, args = command
                result = _run_in_gmsh_session(fn, *args)
                connection.send(("done", result))
            except (EOFError, BrokenPipeError):
                return
            except BaseException as exc:  # noqa: BLE001 - report, never die on a Python error
                try:
                    connection.send(_error_payload(exc))
                except (EOFError, BrokenPipeError, OSError):
                    return
    finally:
        connection.close()


# --------------------------------------------------------------- the parent


class MesherChildHost:
    """Own one reusable mesher child; run builds on it with a wall-clock limit."""

    def __init__(
        self,
        *,
        process_context: multiprocessing.context.BaseContext | None = None,
        target: Callable[[Connection, str | None], None] = _child_main,
    ) -> None:
        self._context = process_context or multiprocessing.get_context("spawn")
        self._target = target
        self._connection: Connection | None = None
        self._process: multiprocessing.Process | None = None
        self._job: Any = None
        self._warm_pending = False
        self._closed = False
        self._lock = threading.RLock()  # one build at a time, as on the gmsh thread

    # process management -------------------------------------------------
    def _start_locked(self) -> Connection:
        process, connection = self._process, self._connection
        if process is not None and process.is_alive() and connection is not None:
            return connection
        self._kill_locked()
        from server.platform.temp_session import temporary_directory_root

        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=self._target,
            args=(child, temporary_directory_root()),
            name="hornlab-mesher-child",
            daemon=True,
        )
        process.start()
        child.close()
        self._job = confine_to_windows_job(process.pid) if process.pid else None
        self._connection, self._process = parent, process
        return parent

    def _kill_locked(self) -> None:
        connection, self._connection = self._connection, None
        process, self._process = self._process, None
        job, self._job = self._job, None
        self._warm_pending = False
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is not None:
            if process.is_alive():
                process.kill()
            process.join(_JOIN_SECONDS)
        if job is not None:
            job.terminate()
            job.close()

    def prewarm(self) -> None:
        """Start the child and queue its import warmup without waiting for it."""

        with self._lock:
            if self._closed or self._warm_pending:
                return
            try:
                connection = self._start_locked()
                connection.send(("warm",))
            except (BrokenPipeError, EOFError, OSError):
                return
            self._warm_pending = True

    def close(self) -> None:
        with self._lock:
            self._closed = True
        # Do not wait for a running build's thread to give the lock back: the
        # kill is what ends it.
        connection, process = self._connection, self._process
        try:
            if connection is not None:
                connection.close()
        except OSError:
            pass
        if process is not None and process.is_alive():
            process.kill()
            process.join(_JOIN_SECONDS)
        with self._lock:
            self._kill_locked()

    def _kill_and_respawn(self) -> None:
        self._kill_locked()
        if not self._closed:
            self.prewarm()

    # one build ------------------------------------------------------------
    def _run_blocking(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        cancel_cb: Callable[[], None] | None,
        timeout: float,
        abort: threading.Event,
    ) -> Any:
        while not self._lock.acquire(timeout=_POLL_SECONDS):
            self._poll_cancel(cancel_cb, abort, locked=False)
        try:
            if self._closed:
                raise RuntimeError("mesher child is shutting down; submission rejected")
            self._poll_cancel(cancel_cb, abort, locked=True)
            connection = self._start_locked()
            # A warmup still in flight is answered first; consume its reply.
            self._send(connection, ("run", fn, args))
            deadline = time.monotonic() + timeout  # the clock starts at the send
            while True:
                if time.monotonic() > deadline:
                    self._kill_and_respawn()
                    raise MesherCrashError(
                        self._message(f"it did not finish within {timeout:g} s and was stopped")
                    )
                self._poll_cancel(cancel_cb, abort, locked=True)
                try:
                    ready = connection.poll(_POLL_SECONDS)
                    event = connection.recv() if ready else None
                except (EOFError, OSError, ConnectionError):
                    if self._closed:
                        raise RuntimeError("mesher child is shutting down") from None
                    raise self._crashed_locked() from None
                if event is None:
                    process = self._process
                    if process is not None and not process.is_alive() and not connection.poll():
                        raise self._crashed_locked()
                    continue
                kind = event[0]
                if kind == "warm":
                    self._warm_pending = False
                    continue
                if kind == "done":
                    return event[1]
                if kind == "error":
                    _, blob, name, message, tb = event
                    log.debug("mesher child raised %s:\n%s", name, tb)
                    if blob is not None:
                        raise pickle.loads(blob)
                    raise MesherChildError(f"{name}: {message}")
        finally:
            self._lock.release()

    @staticmethod
    def _send(connection: Connection, message: tuple[Any, ...]) -> None:
        try:
            connection.send(message)
        except (BrokenPipeError, EOFError, ConnectionError):
            # The warm child died between spawn and send: the next poll sees it.
            return

    def _poll_cancel(
        self,
        cancel_cb: Callable[[], None] | None,
        abort: threading.Event,
        *,
        locked: bool,
    ) -> None:
        try:
            if abort.is_set():
                raise asyncio.CancelledError()
            if self._closed:
                raise RuntimeError("mesher child is shutting down")
            if cancel_cb is not None:
                cancel_cb()
        except BaseException:
            if locked:
                self._kill_and_respawn()
            raise

    def _crashed_locked(self) -> MesherCrashError:
        process = self._process
        exitcode: int | None = None
        if process is not None:
            process.join(_JOIN_SECONDS)
            exitcode = process.exitcode
        reason = _describe_exit(exitcode)
        self._kill_and_respawn()
        return MesherCrashError(self._message(f"the mesher process died ({reason})"))

    @staticmethod
    def _message(cause: str) -> str:
        return (
            f"The mesher crashed or timed out on this geometry: {cause}. "
            f"{SURFACE_FIT_HINT}"
        )

    async def run(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        cancel_cb: Callable[[], None] | None = None,
        timeout: float | None = None,
    ) -> Any:
        abort = threading.Event()
        limit = build_timeout_seconds() if timeout is None else float(timeout)
        task = asyncio.ensure_future(
            asyncio.to_thread(self._run_blocking, fn, args, cancel_cb, limit, abort)
        )
        # A shielded task that fails after its awaiter left must not log "never retrieved".
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The awaiting task went away (client gone, shutdown): end the child
            # so its thread unwinds instead of waiting out the timeout.
            abort.set()
            raise


_host: MesherChildHost | None = None
_host_lock = threading.Lock()


def get_mesher_child() -> MesherChildHost:
    global _host
    with _host_lock:
        if _host is None:
            _host = MesherChildHost()
            atexit.register(close_mesher_child)
        return _host


def close_mesher_child() -> None:
    """Kill the shared child, if any. Safe to call repeatedly."""

    global _host
    with _host_lock:
        host, _host = _host, None
    if host is not None:
        host.close()


def prewarm_mesher_child() -> None:
    """Start the shared child in the background; a no-op when children are off."""

    if child_enabled() and os.environ.get("WG2_MESH_CHILD_PREWARM") != "0":
        get_mesher_child().prewarm()


async def run_mesh_build(
    fn: Callable[..., Any],
    /,
    *args: Any,
    cancel_cb: Callable[[], None] | None = None,
) -> Any:
    """Run ``fn(*args)`` where it can be killed: the mesher child.

    ``fn`` and ``args`` must be picklable. With the child switched off the call
    runs on the in-process gmsh worker thread and ``fn`` is given ``cancel_cb``.
    """

    if child_enabled():
        return await get_mesher_child().run(fn, *args, cancel_cb=cancel_cb)
    from server.mesh.gmsh_worker import run_on_gmsh_worker

    if cancel_cb is not None:
        return await run_on_gmsh_worker(fn, *args, cancel_cb=cancel_cb)
    return await run_on_gmsh_worker(fn, *args)


__all__ = [
    "MesherChildError",
    "MesherCrashError",
    "MesherChildHost",
    "SURFACE_FIT_HINT",
    "close_mesher_child",
    "get_mesher_child",
    "prewarm_mesher_child",
    "run_mesh_build",
]
