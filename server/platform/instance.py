"""Single-instance coordination and local server port selection."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Mapping, Sequence

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Without prototypes ctypes assumes every argument and the return value is a
    # C int. A HANDLE is pointer-sized, so the returned handle would be
    # truncated on 64-bit Windows and then closed by its truncated value.
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForMultipleObjects.argtypes = (
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
    )
    _kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
    _kernel32.CreateEventW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
    _kernel32.SetEvent.restype = wintypes.BOOL
    _kernel32.ResetEvent.argtypes = (wintypes.HANDLE,)
    _kernel32.ResetEvent.restype = wintypes.BOOL
    # FindFirstChangeNotification is the cheapest directory watch that hands
    # back a plain waitable HANDLE, which is what lets it join a process handle
    # in one WaitForMultipleObjects; ReadDirectoryChangesW would say *what*
    # changed, at the cost of an OVERLAPPED buffer for an answer nothing here
    # needs. Its failure value is INVALID_HANDLE_VALUE, not NULL, so it needs a
    # different emptiness test from every other call in this block.
    _kernel32.FindFirstChangeNotificationW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    _kernel32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
    _kernel32.FindNextChangeNotification.argtypes = (wintypes.HANDLE,)
    _kernel32.FindNextChangeNotification.restype = wintypes.BOOL
    _kernel32.FindCloseChangeNotification.argtypes = (wintypes.HANDLE,)
    _kernel32.FindCloseChangeNotification.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
else:
    import fcntl


DEFAULT_PORT = 3100
PORT_ENV = "WG2_PORT"
PORT_SCAN_COUNT = 9
LOCK_FILENAME = "server.pid"

# Windows descriptors are text mode unless asked otherwise, which would turn the
# metadata's trailing newline into CRLF. The lock file reads back byte-for-byte
# on every platform instead.
LOCK_OPEN_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)

# Windows byte-range locks are mandatory and start at the current file position,
# so the locked byte sits past anything the metadata will ever occupy. Locking
# offset 0 measurably breaks both directions: the owner is denied the truncate
# in update_port, and every other process is denied the read that names the
# running instance, so the conflict message loses its pid and port. POSIX flock
# takes the whole file and ignores the offset.
LOCK_BYTE_OFFSET = 1 << 30

# Win32 constants for the liveness probe below.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# WaitForSingleObject needs SYNCHRONIZE; a query-only handle makes it fail
# with WAIT_FAILED, which is indistinguishable from "still running".
SYNCHRONIZE = 0x00100000
ERROR_ACCESS_DENIED = 5
STILL_ACTIVE = 259
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
# ctypes hands a HANDLE back as an unsigned pointer-sized int, so the (HANDLE)-1
# that FindFirstChangeNotification returns on failure arrives as all-ones rather
# than as -1.
INVALID_HANDLE_VALUE = (
    (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1 if sys.platform == "win32" else -1
)
# A control file appearing, vanishing or being renamed is a name change; the
# last-write flag additionally covers a watcher that was armed while the file
# was being filled in rather than created atomically.
FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010

log = logging.getLogger("wg.instance")


def lock_exclusive(descriptor: int) -> None:
    """Take the instance lock without blocking; BlockingIOError means held."""

    if sys.platform == "win32":
        os.lseek(descriptor, LOCK_BYTE_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            # A contended range is reported as EACCES. Anything else is a real
            # filesystem fault and belongs in the caller's hard-error path.
            if exc.errno != errno.EACCES:
                raise
            raise BlockingIOError(exc.errno, exc.strerror or "instance lock held") from None
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(descriptor: int) -> None:
    """Release a lock taken by :func:`lock_exclusive`."""

    if sys.platform == "win32":
        os.lseek(descriptor, LOCK_BYTE_OFFSET, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class InstanceInfo:
    pid: int
    port: int


class InstanceLockError(RuntimeError):
    """Base error for instance-lock failures."""


class InstanceAlreadyRunning(InstanceLockError):
    """Raised when a live process owns the instance lock."""

    def __init__(self, info: InstanceInfo, path: Path):
        self.info = info
        self.path = path
        if info.pid > 0 and info.port > 0:
            message = (
                f"Waveguide Generator is already running (pid {info.pid}, "
                f"port {info.port}; lock {path}). Close that instance or use it at "
                f"http://127.0.0.1:{info.port}/."
            )
        else:
            message = (
                f"Waveguide Generator is already starting or running (lock {path}); "
                "owner metadata is not available yet."
            )
        super().__init__(message)


# A concise alias is convenient for callers and older launcher prototypes.
LockConflict = InstanceAlreadyRunning


def _windows_pid_is_running(pid: int) -> bool:
    """Answer whether a pid is live, without asking ``os.kill``.

    A handle answers for a process whose handle someone still holds: Win32
    keeps the process object resolvable until the last handle closes, so an
    exited process stays addressable and ``os.kill(pid, 0)`` reports it as
    running. That, plus the bare ``OSError`` (WinError 87) it raises for a pid
    that never existed, is why it is the wrong probe here.

    It is *not* the wrong probe because it kills: signal 0 is special-cased and
    leaves the process alone (measured on Windows 2026-08-22; an earlier comment
    here claimed otherwise and was read and reasoned from in good faith). Every
    *other* signal does map to ``TerminateProcess(handle, sig)`` and really does
    terminate -- ``SIGTERM`` leaves exit code 15 -- so do not reduce this to
    "os.kill is harmless on Windows" either.
    """

    # SYNCHRONIZE is what WaitForSingleObject needs below. Ask for it, but do
    # not require it: a process we may only query is still one we can answer
    # for, just without the unambiguous wait.
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    waitable = bool(handle)
    if not handle:
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # A live process we are not allowed to open still counts as running.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        if waitable:
            # The wait is unambiguous where the exit code is not: a process
            # that exits with 259 is indistinguishable from a running one,
            # because STILL_ACTIVE *is* 259. Signalled means exited.
            state = _kernel32.WaitForSingleObject(handle, 0)
            if state == WAIT_OBJECT_0:
                return False
            if state == WAIT_TIMEOUT:
                return True
        exit_code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def pid_is_running(pid: int) -> bool:
    """Return whether ``pid`` is live without relying on Windows ``os.kill`` semantics."""

    return _pid_is_running(pid)


# --------------------------------------------------------------------------
# Blocking waits
#
# ``pid_is_running`` answers "right now?", which is the wrong question for a
# watchdog: asking it on a timer costs a wakeup per tick forever, and on Windows
# each tick is an OpenProcess / WaitForSingleObject / CloseHandle round trip
# through ctypes. What a watchdog actually wants is "tell me when", and Win32
# already has that -- a process object is signalled on exit, so a handle opened
# with SYNCHRONIZE can simply be waited on. The helpers below turn the states a
# watchdog cares about into waitable handles so the thread can park with no
# timer at all, and still react the instant something happens rather than up to
# one poll interval late.
#
# Outcomes are returned as strings so a caller can log them and so a test can
# assert on one without importing Win32 numerology. Only PID_EXITED is a
# conclusion; the other three all mean "look again".
# --------------------------------------------------------------------------

PID_EXITED = "exited"
STOP_REQUESTED = "stopped"
WAKEUP_SIGNALLED = "woken"
WAIT_ELAPSED = "elapsed"

# The historical status-watchdog tick, and still the fallback everywhere a
# waitable stop or a waitable directory is unavailable.
DEFAULT_PID_POLL_INTERVAL = 0.15


class StopSignal(threading.Event):
    """A stop flag that a Win32 wait can block on, not only Python code.

    ``threading.Event`` is a Python flag guarded by a condition variable, and no
    Win32 wait function can observe either. A thread parked in
    ``WaitForMultipleObjects`` therefore learns that a plain event was set only
    by timing out and looking -- which is exactly the periodic wakeup this
    section exists to remove. Pairing the Python flag with a real kernel event
    lets the same wait be released the moment shutdown is requested, so the
    waiter can block indefinitely and still stop promptly.

    This stays a fully ordinary ``threading.Event`` in every other respect, and
    on non-Windows it is nothing else. Callers may keep passing a plain
    ``Event`` where a ``StopSignal`` is accepted; :func:`wait_for_pid_exit`
    detects that and substitutes a bounded wait, trading the power win back for
    correctness.
    """

    def __init__(self) -> None:
        super().__init__()
        self._win32_handle = 0
        if sys.platform == "win32":
            # Manual reset: a stop is sticky and every waiter must see it, not
            # just whichever one the kernel happens to release first.
            handle = _kernel32.CreateEventW(None, True, False, None)
            if not handle:
                # A process this short of handles has larger problems, but a
                # watchdog that degrades to a bounded wait is better than one
                # that refuses to start.
                log.warning(
                    "Could not create a waitable stop event (WinError %d); the status "
                    "watchdog will fall back to polling",
                    ctypes.get_last_error(),
                )
            self._win32_handle = int(handle or 0)

    @property
    def win32_handle(self) -> int:
        """The kernel event handle, or ``0`` where there is none."""

        return self._win32_handle

    def set(self) -> None:
        # Python flag first: a thread released by the kernel event must never
        # be able to observe the handle signalled but ``is_set()`` still false.
        super().set()
        if self._win32_handle:
            _kernel32.SetEvent(self._win32_handle)

    def clear(self) -> None:
        super().clear()
        if self._win32_handle:
            _kernel32.ResetEvent(self._win32_handle)

    def close(self) -> None:
        """Release the kernel event; the Python flag keeps working afterwards.

        Only safe once no thread can still be waiting on the handle. Closing a
        handle out from under a live ``WaitForMultipleObjects`` is undefined
        behaviour on Win32, and the handle number can be reused by the next
        object the process opens, so a long-lived signal is better leaked to
        process teardown than closed at the wrong moment.
        """

        handle, self._win32_handle = self._win32_handle, 0
        if handle:
            _kernel32.CloseHandle(handle)


class DirectoryChangeWakeup:
    """A waitable that fires when the entries of one directory change.

    It reports only *that* something changed, never what, so the owner re-checks
    whatever condition it actually cares about after each wake. That is a good
    trade when the check is a single ``is_file()`` against a directory holding a
    handful of control files, and it is the whole reason a watchdog can stop
    calling ``stat`` on a timer.

    Instances are created by :func:`watch_directory_entries` and are owned by
    the thread that waits on them: :meth:`rearm` is called from inside the wait,
    and :meth:`close` must not run while another thread is still waiting.
    """

    __slots__ = ("_handle", "path")

    def __init__(self, handle: int, path: str):
        self._handle = handle
        self.path = path

    @property
    def handle(self) -> int:
        """The waitable notification handle, or ``0`` once closed."""

        return self._handle

    def rearm(self) -> bool:
        """Request the next notification; ``False`` means the watch is dead.

        Win32 requires this after every signalled wait, and the window between
        the wait returning and this call is one in which changes go unreported.
        Callers therefore rearm *before* re-testing their condition, so a file
        that appears during the handover is still caught by the test that
        follows rather than being missed by both.
        """

        if not self._handle:
            return False
        if _kernel32.FindNextChangeNotification(self._handle):
            return True
        log.debug(
            "Could not rearm the change notification for %s (WinError %d)",
            self.path,
            ctypes.get_last_error(),
        )
        self.close()
        return False

    def close(self) -> None:
        handle, self._handle = self._handle, 0
        if handle:
            # Notification handles come from the Find* family and are closed by
            # it, not by CloseHandle.
            _kernel32.FindCloseChangeNotification(handle)

    def __enter__(self) -> "DirectoryChangeWakeup":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def watch_directory_entries(directory: str | os.PathLike[str]) -> DirectoryChangeWakeup | None:
    """Return a waitable for name and content changes in ``directory``, or ``None``.

    ``None`` means "not watchable here", and every caller must keep a polling
    fallback for it: the directory may not exist yet, may live on a filesystem
    that cannot report changes, or -- most often -- this may simply not be
    Windows. inotify and kqueue could do the same job on Linux and macOS, but
    each needs its own descriptor plumbing and its own failure modes, and the
    callers here already have a correct poll to fall back to.
    """

    if sys.platform != "win32":
        return None
    try:
        path = os.fspath(Path(directory))
    except TypeError:
        return None
    handle = _kernel32.FindFirstChangeNotificationW(
        path,
        False,  # this directory only; the control files are never nested
        FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE,
    )
    if not handle or int(handle) == INVALID_HANDLE_VALUE:
        log.debug(
            "Cannot watch %s for changes (WinError %d); falling back to polling",
            path,
            ctypes.get_last_error(),
        )
        return None
    return DirectoryChangeWakeup(int(handle), path)


def _wait_milliseconds(timeout: float | None) -> int:
    """Convert a seconds timeout to the DWORD the Win32 wait functions take."""

    if timeout is None:
        return INFINITE
    if timeout <= 0:
        return 0
    # INFINITE is just the largest DWORD, so a caller asking for a 50-day
    # timeout must not silently be given a wait that never expires.
    return min(int(timeout * 1000), INFINITE - 1)


def _windows_wait_for_pid_exit(
    pid: int | None,
    stop: threading.Event | None,
    timeout: float | None,
    wakeups: Sequence[DirectoryChangeWakeup | None],
    poll_interval: float,
) -> str | None:
    """One ``WaitForMultipleObjects`` over the process, the stop and the wakeups.

    Returns ``None`` when there is nothing waitable to arm -- no synchronisable
    process handle and no other handle either -- which tells the caller to fall
    back to the portable poll. That path also covers the pid that has already
    gone: ``OpenProcess`` fails for it, and the poll answers correctly and at
    once.
    """

    process_handle = 0
    if pid is not None and pid > 0:
        process_handle = int(_kernel32.OpenProcess(SYNCHRONIZE, False, pid) or 0)
        if not process_handle:
            # Either the pid is gone or we may query it but not synchronise on
            # it. Both are questions for the probe, not for a wait we cannot arm.
            return None

    entries: list[tuple[int, str, DirectoryChangeWakeup | None]] = []
    if process_handle:
        entries.append((process_handle, PID_EXITED, None))
    stop_handle = int(getattr(stop, "win32_handle", 0) or 0)
    if stop_handle:
        entries.append((stop_handle, STOP_REQUESTED, None))
    for wakeup in wakeups:
        if wakeup is not None and wakeup.handle:
            entries.append((wakeup.handle, WAKEUP_SIGNALLED, wakeup))
    if not entries:
        if process_handle:
            _kernel32.CloseHandle(process_handle)
        return None

    # A plain threading.Event is invisible to the kernel, so a wait that would
    # otherwise be unbounded has to keep ticking at the poll interval for it.
    # Only this substitution reintroduces timer wakeups, and only for callers
    # that did not hand over a StopSignal.
    effective = timeout
    if stop is not None and not stop_handle:
        effective = poll_interval if timeout is None else min(timeout, poll_interval)

    try:
        handles = (wintypes.HANDLE * len(entries))(*(entry[0] for entry in entries))
        state = _kernel32.WaitForMultipleObjects(
            len(entries), handles, False, _wait_milliseconds(effective)
        )
    finally:
        if process_handle:
            _kernel32.CloseHandle(process_handle)

    if state == WAIT_TIMEOUT:
        if stop is not None and stop.is_set():
            return STOP_REQUESTED
        return WAIT_ELAPSED
    if state == WAIT_FAILED:
        log.debug(
            "WaitForMultipleObjects failed (WinError %d); falling back to polling",
            ctypes.get_last_error(),
        )
        return None
    index = state - WAIT_OBJECT_0
    if not 0 <= index < len(entries):
        # WAIT_ABANDONED_n only arises for mutexes, and none are waited on here.
        return None
    _handle, outcome, wakeup = entries[index]
    if wakeup is not None:
        wakeup.rearm()
    return outcome


def _polling_wait_for_pid_exit(
    pid: int | None,
    stop: threading.Event | None,
    timeout: float | None,
    poll_interval: float,
) -> str:
    """The portable fallback: the same tick this module has always used."""

    deadline = None if timeout is None else time.monotonic() + timeout
    interval = max(float(poll_interval), 0.0)
    watching = pid is not None and pid > 0
    while True:
        if watching and not _pid_is_running(int(pid)):  # type: ignore[arg-type]
            return PID_EXITED
        if stop is not None and stop.is_set():
            return STOP_REQUESTED
        remaining = interval
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                return WAIT_ELAPSED
            remaining = min(interval, left)
        if stop is not None:
            if stop.wait(remaining):
                return STOP_REQUESTED
        elif remaining > 0:
            time.sleep(remaining)
        if not watching:
            # Nothing here can change on its own, and the caller has conditions
            # of its own to re-test. Hand the tick back rather than owning the
            # loop.
            return WAIT_ELAPSED


def wait_for_pid_exit(
    pid: int | None,
    stop: threading.Event | None = None,
    *,
    timeout: float | None = None,
    wakeups: Sequence[DirectoryChangeWakeup | None] = (),
    poll_interval: float = DEFAULT_PID_POLL_INTERVAL,
) -> str:
    """Block until ``pid`` exits, ``stop`` is set, a wakeup fires or time runs out.

    Returns :data:`PID_EXITED`, :data:`STOP_REQUESTED`, :data:`WAKEUP_SIGNALLED`
    or :data:`WAIT_ELAPSED`. ``PID_EXITED`` is authoritative -- it is the kernel
    signalling the process object, or an explicit liveness probe -- while the
    other three only mean the caller should look at its own conditions again.

    ``timeout=None`` asks to wait forever. On Windows, given a :class:`StopSignal`
    and/or wakeups from :func:`watch_directory_entries`, that is literally a wait
    with no timer: zero wakeups until something really happens. Everywhere else,
    and for a caller that passes a plain ``threading.Event``, the wait is bounded
    by ``poll_interval`` and the caller's loop supplies the rest.
    """

    if sys.platform == "win32":
        outcome = _windows_wait_for_pid_exit(pid, stop, timeout, wakeups, poll_interval)
        if outcome is not None:
            return outcome
    if timeout is None:
        # Reaching the poll at all means no kernel wait could take this on, so
        # the only way anything here is discovered is by looking. `timeout=None`
        # asks for a wait with no timer, and a poll with no timer is a loop that
        # tests one condition for ever: a wakeup handle it cannot observe, a
        # plain `Event` no wait can be released by, or simply the caller's own
        # conditions, none of which it re-tests. So the wait is bounded and the
        # tick handed back, which is the contract this function documents and
        # the one `StopSignal` points callers at -- and what `launch/serve.py`,
        # the only caller that passes `timeout=None`, already loops around.
        timeout = poll_interval
    return _polling_wait_for_pid_exit(pid, stop, timeout, poll_interval)


def read_lock_info(path: Path) -> InstanceInfo | None:
    """Read lock metadata; malformed lock files are treated as stale."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return InstanceInfo(pid=int(payload["pid"]), port=int(payload["port"]))
    except FileNotFoundError:
        return None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    except OSError as exc:
        raise InstanceLockError(f"Could not read instance lock {path}: {exc}") from exc


class InstanceLock:
    """A process-lifetime advisory lock with human-readable owner metadata."""

    def __init__(self, locks_dir: str | os.PathLike[str]):
        self.path = Path(locks_dir) / LOCK_FILENAME
        self._owned = False
        self._pid = os.getpid()
        self._descriptor: int | None = None
        # One descriptor with one shared file position is now seeked by locking,
        # unlocking and every metadata rewrite, so concurrent callers could
        # interleave a seek with another's truncate and write a malformed lock.
        # Reentrant because acquire() calls update_port() and release().
        self._guard = threading.RLock()

    @property
    def owned(self) -> bool:
        return self._owned

    def acquire(self, port: int) -> InstanceInfo:
        with self._guard:
            return self._acquire(port)

    def _acquire(self, port: int) -> InstanceInfo:
        if self._owned:
            return self.update_port(port)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, LOCK_OPEN_FLAGS, 0o600)
        except OSError as exc:
            raise InstanceLockError(
                f"Could not open instance lock {self.path}: {exc}. Check that the data "
                "directory is writable, then start again."
            ) from exc
        try:
            lock_exclusive(descriptor)
        except BlockingIOError:
            os.close(descriptor)
            try:
                owner = read_lock_info(self.path)
            except InstanceLockError:
                owner = None
            raise InstanceAlreadyRunning(owner or InstanceInfo(pid=0, port=0), self.path) from None
        except OSError as exc:
            os.close(descriptor)
            raise InstanceLockError(f"Could not lock instance file {self.path}: {exc}") from exc

        self._descriptor = descriptor
        self._owned = True
        try:
            return self.update_port(port)
        except BaseException:
            self.release()
            raise

    def update_port(self, port: int) -> InstanceInfo:
        """Rewrite metadata while retaining the same locked descriptor."""

        with self._guard:
            descriptor = self._descriptor
            if not self._owned or descriptor is None:
                raise InstanceLockError("Cannot update instance metadata before acquiring the lock")
            info = InstanceInfo(pid=self._pid, port=requested_port(port, environ={}))
            payload = (
                json.dumps({"pid": info.pid, "port": info.port}, sort_keys=True) + "\n"
            ).encode()
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.ftruncate(descriptor, 0)
                written = 0
                while written < len(payload):
                    count = os.write(descriptor, payload[written:])
                    if count <= 0:
                        raise OSError("instance lock metadata write made no progress")
                    written += count
                os.fsync(descriptor)
            except OSError as exc:
                raise InstanceLockError(
                    f"Could not write instance lock metadata {self.path}: {exc}"
                ) from exc
            return info

    def release(self) -> None:
        with self._guard:
            if not self._owned:
                return
            descriptor, self._descriptor = self._descriptor, None
            if descriptor is not None:
                try:
                    unlock(descriptor)
                except OSError:
                    log.exception("Could not unlock instance file %s", self.path)
                finally:
                    os.close(descriptor)
            self._owned = False

    def __enter__(self) -> "InstanceLock":
        if not self._owned:
            raise RuntimeError("Call InstanceLock.acquire(port) before entering its context")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def requested_port(cli_port: int | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    """Resolve and validate CLI/environment/default port precedence."""

    env = os.environ if environ is None else environ
    raw: int | str = cli_port if cli_port is not None else env.get(PORT_ENV, DEFAULT_PORT)
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid port {raw!r}. Use --port or WG2_PORT with a number from 1 to 65535."
        ) from exc
    if not 1 <= port <= 65535:
        raise ValueError(
            f"Invalid port {port}. Use --port or WG2_PORT with a number from 1 to 65535."
        )
    return port


def _configure_port_bind(sock: socket.socket) -> None:
    """Apply the platform's fail-on-busy policy before binding a server port."""

    if sys.platform == "win32":
        # Winsock's SO_REUSEADDR permits another same-user listener to bind the
        # same address and port. Servers need the inverse policy so the fallback
        # scan observes an occupied candidate instead of sharing it.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    elif os.name == "posix" and sys.platform != "cygwin":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def port_is_available(port: int, host: str = "127.0.0.1") -> bool:
    """Probe whether a local TCP port can currently be bound."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        _configure_port_bind(probe)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def select_port(
    preferred: int,
    *,
    host: str = "127.0.0.1",
    scan_count: int = PORT_SCAN_COUNT,
) -> int:
    """Choose ``preferred`` or one of its next nine ports."""

    last = min(65535, preferred + scan_count)
    for candidate in range(preferred, last + 1):
        if port_is_available(candidate, host):
            if candidate != preferred:
                log.warning(
                    "Port %d is busy; using port %d instead. Open http://%s:%d/ "
                    "or pass --port to choose another port.",
                    preferred,
                    candidate,
                    host,
                    candidate,
                )
            return candidate
    raise OSError(
        f"Ports {preferred} through {last} are all busy on {host}. Stop an existing "
        "server or start with --port PORT using an available port."
    )


def reserve_port(
    preferred: int,
    *,
    host: str = "127.0.0.1",
    scan_count: int = PORT_SCAN_COUNT,
) -> tuple[socket.socket, int]:
    """Bind and retain a local socket, retrying the configured fallback range."""

    last = min(65535, preferred + scan_count)
    for candidate in range(preferred, last + 1):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            _configure_port_bind(listener)
            listener.bind((host, candidate))
        except OSError:
            listener.close()
            continue
        if candidate != preferred:
            log.warning("Port %d is busy; using port %d instead", preferred, candidate)
        return listener, candidate
    raise OSError(
        f"Ports {preferred} through {last} are all busy on {host}. Stop an existing "
        "server or start with --port PORT using an available port."
    )


def acquire_port(
    cli_port: int | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    host: str = "127.0.0.1",
) -> int:
    """Resolve configuration and select an available local port."""

    return select_port(requested_port(cli_port, environ=environ), host=host)
