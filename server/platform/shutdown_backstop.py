"""End a stopping server process on time, even while native code holds a thread.

A stop request makes Uvicorn run the application's shutdown handlers, and
those can finish -- but the *process* still cannot end while a gmsh (OCC) call
or another native call runs on an executor thread, because Python joins
executor threads at interpreter exit. No timeout on ``executor.shutdown()``
changes that: it bounds how long a coroutine waits, not how long the process
lives.

The packaged launcher already bounds Quit from outside. It kills the server's
process tree after its 8 s ``shutdown_timeout``
(``launchers/statusapp/controller.py``). But then none of the server's own
cleanup runs, and two paths have no outer bound at all: a server run directly
in a terminal, and, on macOS and Linux, one orphaned by a launcher crash.

So every stop path in ``launch/serve.py`` begins a budget here, below the
launcher's grace. The shutdown steps that wait (the job runtime's checkpoint
wait, the gmsh join) ask :func:`shutdown_wait_limit` how long they may. A
watchdog thread ends the process with ``os._exit`` when the budget runs out,
or at once when asked to. It logs and flushes first, on helper threads it
waits for at most ``FLUSH_TIMEOUT_SECONDS``, so a wedged log handler can delay
the exit by that much but never hold it. None of that runs inside the signal
handler that may have asked for the exit.

``os._exit`` skips ``atexit`` handlers and every Python finalizer. Everything it
can skip is crash-safe already:
- SQLite commits are atomic.
- A job's results are published only by the single ``complete_job`` transaction.
- Owned child processes were closed earlier in the budget, and each one also
  leaves when its parent does.
- The instance lock is an OS lock that is released with the process.

Nothing is armed until a stop is requested, so a process that builds the app
without serving it (tests, embedders, the CLI) never starts a watchdog and
keeps the waits it always had.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
import sys
import threading
import time


#: Total time a stopping server gives its own cleanup, counted from the stop
#: request. Below the status window's 8 s ``shutdown_timeout``, so a Quit ends
#: with the server's own exit rather than the launcher's tree kill.
DEFAULT_SHUTDOWN_BUDGET_SECONDS = 5.0

#: The end of the budget kept for the steps after the job runtime and the gmsh
#: worker stop waiting: closing the BEMPP child, detaching BEAT, releasing the
#: instance lock and flushing the logs.
CLEANUP_RESERVE_SECONDS = 1.5

#: How long the watchdog waits for its last log line and the log flush before
#: exiting regardless.
FLUSH_TIMEOUT_SECONDS = 1.0

#: A stop was requested and the process stopped. The Windows launcher script
#: pauses its console on any status other than 0 and 2, and nothing reads a
#: distinct code; the log says which way the process ended.
BACKSTOP_EXIT_CODE = 0

log = logging.getLogger("wg.launch")


def _end_process(code: int) -> None:
    """End this process now, running no cleanup at all.

    On Windows ``os._exit`` reaches ``ExitProcess``. That terminates the other
    threads and then runs every loaded DLL's detach code. A thread killed inside
    OCC, TBB or LLVM while holding the loader or heap lock can deadlock that
    detach, and hanging is exactly what this exit exists to prevent.
    ``TerminateProcess`` on the current process runs no detach code; it is
    what the launcher's Job Object does to the server anyway. ``os._exit``
    stays as the fallback, and it is the whole answer on POSIX.
    """

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
            kernel32.TerminateProcess.restype = wintypes.BOOL
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
        except Exception:  # noqa: BLE001 - fall through to the portable exit
            pass
    os._exit(code)


#: Looked up when the watchdog fires, not when a backstop is built, so the
#: server test suite can replace it once for the whole run and no backstop a
#: test arms by accident can end the test process (``server/tests/conftest.py``).
_process_exit: Callable[[int], None] = _end_process

_active: ShutdownBackstop | None = None


def _flush_logs() -> None:
    from server.platform.logging_setup import flush_logs

    flush_logs()


class ShutdownBackstop:
    """A deadline for one process's shutdown, and the exit that enforces it."""

    def __init__(
        self,
        budget_seconds: float = DEFAULT_SHUTDOWN_BUDGET_SECONDS,
        *,
        exit_process: Callable[[int], None] | None = None,
        flush: Callable[[], None] | None = None,
        flush_timeout_seconds: float = FLUSH_TIMEOUT_SECONDS,
        exit_code: int = BACKSTOP_EXIT_CODE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget_seconds = float(budget_seconds)
        self.flush_timeout_seconds = float(flush_timeout_seconds)
        self.exit_code = exit_code
        self._exit_process = exit_process
        self._flush = flush if flush is not None else _flush_logs
        self._clock = clock
        # Reentrant because a second signal can arrive while the handler for
        # the first is still inside ``begin``.
        self._lock = threading.RLock()
        self._deadline: float | None = None
        self._reason = ""
        self._exit_reason: str | None = None
        self._exit_now = threading.Event()
        self._fired = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def begun(self) -> bool:
        return self._deadline is not None

    @property
    def fired(self) -> bool:
        """Whether the watchdog has called the exit (tests only; a real exit never returns)."""

        return self._fired.is_set()

    def remaining(self) -> float | None:
        """Seconds left in the budget, or ``None`` before a stop was requested."""

        deadline = self._deadline
        if deadline is None:
            return None
        return max(0.0, deadline - self._clock())

    def activate(self) -> None:
        """Make this the budget :func:`shutdown_wait_limit` answers from."""

        global _active
        _active = self

    def deactivate(self) -> None:
        """Stop answering :func:`shutdown_wait_limit`.

        This does not disarm a begun watchdog. Once a stop has begun, the
        process ends by its deadline, which is why only a process's real launch
        path may call ``begin``.
        """

        global _active
        if _active is self:
            _active = None

    def begin(self, reason: str) -> bool:
        """Start the budget. Only the first call counts; later ones return False.

        Safe from a signal handler, a watchdog thread or a Win32 console
        callback: it records the deadline and starts the thread, and leaves all
        logging to that thread's helpers.
        """

        with self._lock:
            if self._deadline is not None:
                return False
            self._deadline = self._clock() + self.budget_seconds
            self._reason = reason
            thread = threading.Thread(
                target=self._watch, name="wg2-shutdown-backstop", daemon=True
            )
            self._thread = thread
        thread.start()
        return True

    def exit_now(self, reason: str) -> None:
        """Skip the rest of the budget: flush the logs and end the process now.

        The exit itself happens on the watchdog thread, so this returns at once
        and is safe wherever ``begin`` is.
        """

        self._exit_reason = reason
        self.begin(reason)
        self._exit_now.set()

    def _watch(self) -> None:
        # This thread never logs itself. A handler wedged on a dead disk or
        # console blocks whichever thread calls it, and this is the one that
        # has to reach the exit.
        announcement = threading.Thread(
            target=log.info,
            args=(
                "Shutdown requested (%s); this process will exit within %.1f s",
                self._reason,
                self.budget_seconds,
            ),
            name="wg2-shutdown-log",
            daemon=True,
        )
        announcement.start()
        remaining = self.remaining()
        if self._exit_now.wait(remaining if remaining is not None else 0.0):
            last: tuple[object, ...] = ("Exiting now: %s", self._exit_reason)
        else:
            last = (
                "Shutdown did not finish within its %.1f s budget; exiting without "
                "waiting for the rest of it",
                self.budget_seconds,
            )
        self._log_and_flush_within_timeout(announcement, last)
        self._fired.set()
        exit_process = self._exit_process if self._exit_process is not None else _process_exit
        exit_process(self.exit_code)

    def _log_and_flush_within_timeout(
        self, announcement: threading.Thread, last: tuple[object, ...]
    ) -> None:
        def finish() -> None:
            # After the announcement, so the log keeps its order even when a
            # slow handler held it.
            announcement.join()
            try:
                log.warning(*last)
                self._flush()
            except Exception:  # noqa: BLE001 - the exit must not depend on the logs
                pass

        # A handler wedged on a dead disk or console must not hold the exit it
        # is only meant to precede.
        finisher = threading.Thread(target=finish, name="wg2-shutdown-flush", daemon=True)
        finisher.start()
        finisher.join(self.flush_timeout_seconds)


def shutdown_wait_limit(default: float | None) -> float | None:
    """How long one shutdown step may wait.

    Returns ``default`` (``None`` meaning "unbounded") while no stop budget is
    running, so tests, embedders and the CLI keep the waits they always had.
    Once a stop has begun, it returns the smaller of ``default`` and the budget
    left after ``CLEANUP_RESERVE_SECONDS``, and never less than zero.
    """

    backstop = _active
    remaining = None if backstop is None else backstop.remaining()
    if remaining is None:
        return default
    limit = max(0.0, remaining - CLEANUP_RESERVE_SECONDS)
    return limit if default is None else min(default, limit)


__all__ = [
    "BACKSTOP_EXIT_CODE",
    "CLEANUP_RESERVE_SECONDS",
    "DEFAULT_SHUTDOWN_BUDGET_SECONDS",
    "FLUSH_TIMEOUT_SECONDS",
    "ShutdownBackstop",
    "shutdown_wait_limit",
]
