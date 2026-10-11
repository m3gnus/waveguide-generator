"""One backend owner per data folder: find it, or start it, exactly once.

A client (the CLI, the Fusion add-in launcher) that wants to submit work needs the
backend that owns a data folder. The ownership rules are:

* The owner is whichever process holds the ``InstanceLock`` on
  ``<data>/locks/server.pid``. The lock is an OS lock released by the kernel when
  the holder dies, so a leftover metadata file never counts as an owner.
* A claimed owner is only trusted after ``GET /health`` on the port it recorded
  reports the same data folder and the same pid. A mismatch is a refusal with a
  reason, never a reason to start a second backend or use a private data folder.
* Starting is serialised by a blocking lock on ``<data>/locks/backend-start.lock``.
  The second of two concurrent clients waits, re-discovers under the lock and
  finds the backend the first one started.
* A spawned backend that exits 2 lost the ``InstanceLock`` race to another
  starter (for instance the GUI launcher); its winner is adopted.

Stdlib plus ``server.platform.instance`` only, so a CLI can import this without
loading the server application.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .instance import (
    LOCK_FILENAME,
    LOCK_OPEN_FLAGS,
    InstanceInfo,
    lock_exclusive,
    read_lock_info,
    unlock,
)

START_LOCK_FILENAME = "backend-start.lock"
LOG_FILENAME = "backend-start.log"
HEALTH_TIMEOUT = 2.0
POLL_INTERVAL = 0.1
# A starter that exits 2 should have a live winner within moments. If none shows
# up the exit was a probe collision (see _lock_is_held), not a real owner.
ADOPT_GRACE = 5.0
MAX_SPAWN_ATTEMPTS = 3
EXIT_ALREADY_RUNNING = 2


@dataclass(frozen=True, slots=True)
class BackendHandle:
    base_url: str
    pid: int
    port: int
    data_dir: Path
    version: str
    commit: str


class BackendOwnerError(RuntimeError):
    """Base class; the message always says what to do next."""


class BackendDataDirMismatch(BackendOwnerError):
    """A live owner answers, but not for this data folder / pid."""


class BackendStartTimeout(BackendOwnerError):
    """The spawned backend did not become healthy in time."""


class BackendSpawnError(BackendOwnerError):
    """The backend could not be started."""


def _locks_dir(data_dir: Path) -> Path:
    return data_dir / "locks"


def _lock_is_held(path: Path) -> bool:
    """Same liveness test as ``InstanceLock``: try the non-blocking lock.

    Not pid existence: pids are reused and a crashed owner leaves its file behind.
    The probe holds the lock for an instant, which can make a backend starting at
    that very moment see exit 2; ``ensure_backend`` tolerates that.
    """

    try:
        descriptor = os.open(path, LOCK_OPEN_FLAGS, 0o600)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    try:
        try:
            lock_exclusive(descriptor)
        except BlockingIOError:
            return True
        except OSError:
            return False
        try:
            unlock(descriptor)
        except OSError:
            pass
        return False
    finally:
        os.close(descriptor)


def _fetch_health(port: int) -> dict[str, object] | None:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health")
    try:
        with urllib.request.urlopen(request, timeout=HEALTH_TIMEOUT) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def _same_path(left: object, right: Path) -> bool:
    try:
        return Path(str(left)).resolve() == right.resolve()
    except (OSError, ValueError):
        return False


def _discover(data_dir: Path) -> BackendHandle | None:
    lock_path = _locks_dir(data_dir) / LOCK_FILENAME
    if not _lock_is_held(lock_path):
        return None
    try:
        info = read_lock_info(lock_path)
    except RuntimeError:
        return None
    if info is None or info.port <= 0:
        return None  # holder is still starting; metadata not written yet
    health = _fetch_health(info.port)
    if health is None:
        return None  # lock held but not serving (yet)
    return _validate(data_dir, info, health)


def _validate(data_dir: Path, info: InstanceInfo, health: dict[str, object]) -> BackendHandle:
    reported_dir = health.get("data_dir")
    if not _same_path(reported_dir, data_dir):
        raise BackendDataDirMismatch(
            f"The backend on port {info.port} reports data folder {reported_dir!r}, not "
            f"{str(data_dir)!r}. Refusing to use it. Stop that backend or point the client "
            "at its data folder; do not work around this with a different data folder."
        )
    reported_pid = health.get("pid")
    if reported_pid != info.pid:
        raise BackendDataDirMismatch(
            f"The instance lock for {data_dir} names pid {info.pid} on port {info.port}, but "
            f"that port answers as pid {reported_pid!r}. Another program may hold the port. "
            "Stop the backend and start it again."
        )
    return BackendHandle(
        base_url=f"http://127.0.0.1:{info.port}",
        pid=info.pid,
        port=info.port,
        data_dir=data_dir,
        version=str(health.get("version", "")),
        commit=str(health.get("commit", "")),
    )


def discover_backend(data_dir: str | os.PathLike[str]) -> BackendHandle | None:
    """Return the running backend that owns ``data_dir``, or ``None``.

    Raises :class:`BackendDataDirMismatch` when something holds the lock but does
    not identify as this data folder's backend.
    """

    return _discover(Path(data_dir).resolve())


class _StartLock:
    """Blocking cross-process lock with a timeout, built on the instance helpers."""

    def __init__(self, path: Path):
        self.path = path
        self._descriptor: int | None = None

    def acquire(self, deadline: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, LOCK_OPEN_FLAGS, 0o600)
        while True:
            try:
                lock_exclusive(descriptor)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise BackendStartTimeout(
                        f"Another client is starting the backend (lock {self.path}) and it "
                        "did not finish in time. Check the backend log in the data folder's "
                        "logs directory, then try again."
                    ) from None
                time.sleep(POLL_INTERVAL)
                continue
            except OSError:
                os.close(descriptor)
                raise
            self._descriptor = descriptor
            return

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            unlock(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)


def _spawn(argv: Sequence[str], data_dir: Path) -> tuple[subprocess.Popen[bytes], Path]:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILENAME
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        with open(log_path, "ab") as log_file:
            process = subprocess.Popen(  # noqa: S603
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **kwargs,  # type: ignore[arg-type]
            )
    except OSError as exc:
        raise BackendSpawnError(
            f"Could not start the backend ({argv[0]!r}): {exc}. Check the installation, "
            "or start Waveguide Generator manually."
        ) from exc
    return process, log_path


def ensure_backend(
    data_dir: str | os.PathLike[str],
    start_argv: Sequence[str],
    *,
    timeout: float = 60,
) -> BackendHandle:
    """Return the backend owning ``data_dir``, starting it exactly once if needed."""

    root = Path(data_dir).resolve()
    deadline = time.monotonic() + timeout
    found = _discover(root)
    if found is not None:
        return found

    start_lock = _StartLock(_locks_dir(root) / START_LOCK_FILENAME)
    start_lock.acquire(deadline)
    try:
        found = _discover(root)
        if found is not None:
            return found
        return _start_and_wait(root, start_argv, deadline)
    finally:
        start_lock.release()


def _start_and_wait(root: Path, argv: Sequence[str], deadline: float) -> BackendHandle:
    log_path = root / "logs" / LOG_FILENAME
    for _attempt in range(MAX_SPAWN_ATTEMPTS):
        process, log_path = _spawn(argv, root)
        adopt_until: float | None = None
        while True:
            found = _discover(root)
            if found is not None:
                return found
            code = process.poll()
            now = time.monotonic()
            if code is not None:
                if code != EXIT_ALREADY_RUNNING:
                    raise BackendSpawnError(
                        f"The backend exited with code {code} before it was ready. "
                        f"See {log_path}."
                    )
                # Lost the InstanceLock race: wait briefly for the winner to serve.
                if adopt_until is None:
                    adopt_until = min(now + ADOPT_GRACE, deadline)
                if now >= adopt_until:
                    break  # no owner materialised; the exit was a probe collision
            if now >= deadline:
                raise BackendStartTimeout(
                    f"The backend did not become ready within the timeout. See {log_path}."
                )
            time.sleep(POLL_INTERVAL)
    raise BackendSpawnError(
        f"The backend kept exiting with code {EXIT_ALREADY_RUNNING} but no owner is "
        f"serving data folder {root}. Another program may hold the instance lock "
        f"({_locks_dir(root) / LOCK_FILENAME}). See {log_path}."
    )
