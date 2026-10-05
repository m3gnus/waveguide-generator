"""Run OCC mesh builds in a killable child process.

The pinned mesher can abort or segfault inside ``gmsh``'s ``mesh.generate`` on
shapes the UI allows (an ICW design with a rectangle morph, a high fixed part
and shrinkage on), and some variants never return. A native abort on the gmsh
worker *thread* takes the whole server with it, and no pre-check can tell the
failing shapes apart from the working ones, so the build runs in a spawned
child instead:

* a crash (any signal or a nonzero exit) kills only the child and surfaces as
  :class:`MesherCrashError`, a named, user-facing error; the server stays up.
  Nothing is retried automatically (Magnus, 2026-10-05).
* there is no default wall-clock limit: mesh density is the user's choice and a
  fine mesh may legitimately take minutes. A hang is ended by the user's cancel,
  which kills the child. ``WG2_MESH_BUILD_TIMEOUT_S`` sets a limit for
  qualification runs.
* one child stays warm across builds, so the 1-2 s import of gmsh, OCC and
  scipy is paid at boot (in the background) and again only after a death. gmsh
  itself is initialised and finalised around every build, as it always was.
* cancelling a build that is running in the child kills the child, the only
  bounded way to stop OCC. Cancelling before the build is sent leaves it alone.

The parent never blocks on the pipe: a reader thread owns ``recv`` and hands
whole frames over a queue, so the deadline, a cancel and shutdown always win,
even against a child that stalls halfway through a result. Every request and
reply carries an id, and a reply that is not for the build in hand is dropped.

It follows the BEMPP worker's process model (``server/solver/bempp_process.py``):
the ``spawn`` context on every platform, a module-level target, a Windows job
object so the child dies with the server, and a parent-sentinel watchdog. The
packaged app is a relocatable interpreter rather than a frozen executable
(``docs/plans/STANDALONE-APP.md``), so there is no ``freeze_support`` concern.

``WG2_TEST_MESH_IN_PROCESS=1`` runs builds on the in-process gmsh worker thread.
It is for tests that substitute the mesher in ``sys.modules`` (a spawned child
cannot see that) and is ignored in a bundled app (``WG2_BUNDLE=1``).
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import multiprocessing
from multiprocessing.connection import Connection
import os
import pickle
import queue
import signal
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from server.platform.process_tree import confine_to_windows_job

log = logging.getLogger("wg.mesh")

IN_PROCESS_ENV = "WG2_TEST_MESH_IN_PROCESS"
TIMEOUT_ENV = "WG2_MESH_BUILD_TIMEOUT_S"

_POLL_SECONDS = 0.1
_JOIN_SECONDS = 2.0
_PARENT_GONE_EXIT_CODE = 3
_WARM_ID = 0
_READER_JOIN_SECONDS = 5.0

CRASH_MESSAGE = (
    "The mesher crashed on this geometry. A known cause is morph shrinkage with a "
    "fixed part of 0.8 or more: turn off shrinkage or lower the fixed part. "
    "Otherwise try a different morph target."
)


class MesherCrashError(RuntimeError):
    """The mesher process died or ran out of time on this geometry."""


class MesherChildError(RuntimeError):
    """A failure inside the child that could not be sent back as itself."""


def child_enabled() -> bool:
    if os.environ.get("WG2_BUNDLE") == "1":
        return True
    return os.environ.get(IN_PROCESS_ENV, "").strip() != "1"


def build_timeout_seconds() -> float | None:
    """The qualification limit from the environment; ``None`` (no limit) otherwise."""

    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        return None
    return value if value > 0 else None


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
    """Pay the import cost here, in the process that will reuse it."""

    from server.mesh.prewarm import import_mesher_modules

    import_mesher_modules()
    import gmsh  # noqa: F401
    import meshio  # noqa: F401 - parsing a build's artifact needs it

    import server.mesh.builder  # noqa: F401
    import server.exports.core  # noqa: F401


def _error_payload(request_id: int, exc: BaseException) -> tuple[Any, ...]:
    blob: bytes | None
    try:
        blob = pickle.dumps(exc)
        pickle.loads(blob)  # an exception with a custom __init__ can pickle but not load
    except Exception:  # noqa: BLE001
        blob = None
    return ("error", request_id, blob, type(exc).__name__, str(exc), traceback.format_exc())


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
                connection.send(_error_payload(-1, exc))
                continue
            if command is None:
                return
            kind, request_id = command[0], command[1]
            try:
                if kind == "warm":
                    try:
                        _warm_this_process()
                    except Exception:  # noqa: BLE001 - the build reports it where it matters
                        log.info("mesher child warmup failed", exc_info=True)
                    connection.send(("warm", request_id))
                    continue
                from server.mesh.gmsh_worker import _run_in_gmsh_session

                _, _, fn, args = command
                result = _run_in_gmsh_session(fn, *args)
                connection.send(("done", request_id, result))
            except (EOFError, BrokenPipeError):
                return
            except BaseException as exc:  # noqa: BLE001 - report, never die on a Python error
                try:
                    connection.send(_error_payload(request_id, exc))
                except (EOFError, BrokenPipeError, OSError):
                    return
    finally:
        connection.close()


# --------------------------------------------------------------- the parent


class _Channel:
    """One child process and the thread that reads whole frames from it."""

    def __init__(self, process: Any, connection: Connection, job: Any) -> None:
        self.process = process
        self.connection = connection
        self.job = job
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.warm_pending = False
        self.reader = threading.Thread(target=self._read, name="wg2-mesh-reader", daemon=True)
        self.reader.start()

    def _read(self) -> None:
        # This thread owns the parent end of the pipe for its whole life and is
        # the only one to close it, so the descriptor cannot be released (and
        # reused by the next child's pipe) while a read on it is possible.
        try:
            while True:
                try:
                    event = self.connection.recv()
                except (EOFError, OSError):
                    self.events.put(("eof",))
                    return
                except Exception as exc:  # noqa: BLE001 - a result the parent cannot unpickle
                    self.events.put(("badframe", exc))
                    continue
                self.events.put(event)
        finally:
            try:
                self.connection.close()
            except OSError:
                pass

    def kill(self) -> None:
        """Kill the child, then wait for the reader to see EOF and close its end."""

        process = self.process
        try:
            if process.is_alive():
                process.kill()
            process.join(_JOIN_SECONDS)
        except (OSError, ValueError):
            pass
        if self.job is not None:
            self.job.terminate()
            self.job.close()
        # The child is dead, so the pipe reports EOF and the reader leaves on
        # its own. Never close the descriptor from here: the reader may still
        # be about to read it.
        self.reader.join(_READER_JOIN_SECONDS)


class MesherChildHost:
    """Own one reusable mesher child; run builds on it, killing it when asked."""

    def __init__(
        self,
        *,
        process_context: multiprocessing.context.BaseContext | None = None,
        target: Callable[[Connection, str | None], None] = _child_main,
    ) -> None:
        self._context = process_context or multiprocessing.get_context("spawn")
        self._target = target
        self._channel: _Channel | None = None
        self._closed = False
        self._state = threading.RLock()  # short: the process fields, never a build
        self._serial = threading.Lock()  # one build at a time, as on the gmsh thread
        self._ids = iter(range(1, 1 << 62))

    # process management (caller holds _state) --------------------------------
    def _ensure_locked(self) -> _Channel:
        channel = self._channel
        if channel is not None and channel.process.is_alive():
            return channel
        if channel is not None:
            channel.kill()
            self._channel = None
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
        job = confine_to_windows_job(process.pid) if process.pid else None
        self._channel = _Channel(process, parent, job)
        return self._channel

    def _discard(self, channel: _Channel, *, respawn: bool = True) -> None:
        """Kill ``channel`` (if it is still the current one) and warm a new child.

        ``respawn=False`` for a build abandoned because its awaiting task was
        cancelled, which is what a Quit does before it closes the host.
        """

        with self._state:
            if self._channel is channel:
                self._channel = None
            channel.kill()
            if respawn and not self._closed:
                self.prewarm()

    def prewarm(self) -> None:
        """Start the child and queue its import warmup without waiting for it."""

        with self._state:
            if self._closed:
                return
            try:
                channel = self._ensure_locked()
                if channel.warm_pending:
                    return
                channel.connection.send(("warm", _WARM_ID))
                channel.warm_pending = True
            except (BrokenPipeError, EOFError, OSError):
                return

    def close(self) -> None:
        """Kill the child now. Never waits for a running build: the kill ends it."""

        with self._state:
            self._closed = True
            channel, self._channel = self._channel, None
        if channel is not None:
            channel.kill()

    # one build ------------------------------------------------------------
    def _run_blocking(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        cancel_cb: Callable[[], None] | None,
        timeout: float | None,
        abort: threading.Event,
    ) -> Any:
        def checkpoint(channel: _Channel | None) -> None:
            """Raise if the caller cancelled; kill the child only if a build is in it."""

            try:
                if abort.is_set():
                    raise asyncio.CancelledError()
                if self._closed:
                    raise RuntimeError("mesher child is shutting down; build abandoned")
                if cancel_cb is not None:
                    cancel_cb()
            except BaseException:
                if channel is not None:
                    self._discard(channel, respawn=not abort.is_set())
                raise

        while not self._serial.acquire(timeout=_POLL_SECONDS):
            checkpoint(None)
        try:
            checkpoint(None)
            request_id = next(self._ids)
            with self._state:
                if self._closed:
                    raise RuntimeError("mesher child is shutting down; submission rejected")
                channel = self._ensure_locked()
                try:
                    channel.connection.send(("run", request_id, fn, args))
                except (BrokenPipeError, EOFError, OSError):
                    pass  # the reader reports the death as "eof" below
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                if deadline is not None and time.monotonic() > deadline:
                    self._discard(channel)
                    raise MesherCrashError(
                        f"The mesher did not finish within {timeout:g} s and was stopped."
                    )
                checkpoint(channel)
                try:
                    event = channel.events.get(timeout=_POLL_SECONDS)
                except queue.Empty:
                    continue
                kind = event[0]
                if kind == "warm":
                    channel.warm_pending = False
                    continue
                if kind == "eof":
                    if self._closed:
                        raise RuntimeError("mesher child is shutting down; build abandoned")
                    raise self._crashed(channel)
                if kind == "badframe":
                    self._discard(channel)
                    raise MesherChildError(f"The mesher child sent an unreadable result: {event[1]}")
                if event[1] != request_id:
                    log.warning("dropped a mesher child reply for request %s", event[1])
                    continue
                if kind == "done":
                    return event[2]
                if kind == "error":
                    _, _, blob, name, message, tb = event
                    log.debug("mesher child raised %s:\n%s", name, tb)
                    if blob is not None:
                        raise pickle.loads(blob)
                    raise MesherChildError(f"{name}: {message}")
        finally:
            self._serial.release()

    def _crashed(self, channel: _Channel) -> MesherCrashError:
        process = channel.process
        process.join(_JOIN_SECONDS)
        log.error("mesher child died: %s", _describe_exit(process.exitcode))
        self._discard(channel)
        return MesherCrashError(CRASH_MESSAGE)

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
            # so its thread unwinds instead of waiting for a result nobody wants.
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
    "CRASH_MESSAGE",
    "close_mesher_child",
    "get_mesher_child",
    "prewarm_mesher_child",
    "run_mesh_build",
]
