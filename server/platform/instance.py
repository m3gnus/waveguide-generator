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
from typing import Mapping

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
