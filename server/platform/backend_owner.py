"""One backend owner per data folder: find it, or start it, exactly once.

A client (the CLI, the Fusion add-in launcher) submits to the backend owning its
exact data folder. InstanceLock on ``<data>/locks/server.pid``, taken by the
supported backend, is the authority. Discovery never creates or locks that file:
it reads metadata, then confirms that ``GET /health`` reports the same folder
(filesystem identity) and pid. Missing, stale or mismatched answers mean no
confirmed owner yet; the preferred metadata port may change during startup.

Starting is serialised on ``<data>/locks/backend-start.lock``. Exit 2 means
another starter owns InstanceLock: wait for it until the overall deadline without
respawning. A matching-pid backend reporting a different folder is a typed
refusal only at the deadline. Never work around conflicts with a private folder.
A timeout terminates and reaps our unconfirmed child; success leaves it detached.

``start_argv`` must start the supported backend on this exact data folder with
``--no-browser`` (the launcher slice defines that argv later).
Stdlib plus ``server.platform.instance`` only, without loading the application.
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
TERMINATE_GRACE = 1.0
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
    """At the deadline, the metadata pid serves a different data folder."""


class BackendStartTimeout(BackendOwnerError):
    """No confirmed owner became healthy before the deadline."""


class BackendSpawnError(BackendOwnerError):
    """The backend could not be started."""


def _locks_dir(data_dir: Path) -> Path:
    return data_dir / "locks"


def _owner_info(data_dir: Path) -> InstanceInfo | None:
    try:
        return read_lock_info(_locks_dir(data_dir) / LOCK_FILENAME)
    except RuntimeError:
        return None


def _owner_description(data_dir: Path) -> str:
    info = _owner_info(data_dir)
    if info is None:
        return "Instance-lock metadata is not available yet."
    return f"Instance-lock metadata names pid {info.pid}, port {info.port}."


def _fetch_health(port: int) -> dict[str, object] | None:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health")
    try:
        with urllib.request.urlopen(request, timeout=HEALTH_TIMEOUT) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def _same_path(left: object, right: Path) -> bool:
    if not isinstance(left, (str, os.PathLike)):
        return False
    try:
        try:
            return os.path.samefile(left, right)
        except FileNotFoundError:
            return Path(left).resolve() == right.resolve()
    except (OSError, ValueError):
        return False


def _observe(data_dir: Path) -> tuple[InstanceInfo | None, dict[str, object] | None]:
    info = _owner_info(data_dir)
    health = _fetch_health(info.port) if info is not None and info.port > 0 else None
    return info, health


def _validate(
    data_dir: Path, info: InstanceInfo | None, health: dict[str, object] | None
) -> BackendHandle | None:
    if info is None or health is None:
        return None
    if health.get("pid") != info.pid or not _same_path(health.get("data_dir"), data_dir):
        return None
    return BackendHandle(
        base_url=f"http://127.0.0.1:{info.port}",
        pid=info.pid,
        port=info.port,
        data_dir=data_dir,
        version=str(health.get("version", "")),
        commit=str(health.get("commit", "")),
    )


def _discover(data_dir: Path) -> BackendHandle | None:
    return _validate(data_dir, *_observe(data_dir))


def discover_backend(data_dir: str | os.PathLike[str]) -> BackendHandle | None:
    """Confirm the metadata pid's healthy backend for this folder, or return None.

    Never create or acquire server.pid's lock, nor refuse transient mismatches.
    """

    return _discover(Path(data_dir).resolve())


class _StartLock:
    """Blocking cross-process lock with a timeout, built on the instance helpers."""

    def __init__(self, path: Path):
        self.path = path
        self._descriptor: int | None = None

    def acquire(self, deadline: float) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, LOCK_OPEN_FLAGS, 0o600)
        except OSError as exc:
            raise BackendOwnerError(
                f"Could not open backend start lock {self.path}: {exc}. "
                "Check that the data folder is writable and its locks directory is accessible."
            ) from exc
        while True:
            try:
                lock_exclusive(descriptor)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise BackendStartTimeout(
                        f"Another client is starting the backend (lock {self.path}) and it "
                        f"did not finish in time. {_owner_description(self.path.parent.parent)} "
                        "Check the backend log in the data folder's logs directory, then try again."
                    ) from None
                time.sleep(POLL_INTERVAL)
                continue
            except OSError as exc:
                os.close(descriptor)
                raise BackendOwnerError(
                    f"Could not acquire backend start lock {self.path}: {exc}. "
                    "Check permissions and filesystem locking support for the data folder."
                ) from exc
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
    log_path = log_dir / LOG_FILENAME
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
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


def _log_tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 4096))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return "(start log unavailable)"


def _terminate_child(process: subprocess.Popen[bytes]) -> str:
    if process.poll() is not None:
        return ""
    try:
        process.terminate()
    except ProcessLookupError:
        pass  # The child exited between poll() and terminate(); still reap it.
    try:
        process.wait(timeout=TERMINATE_GRACE)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return f" Terminated and waited for unconfirmed spawned child pid {process.pid}."


def ensure_backend(
    data_dir: str | os.PathLike[str],
    start_argv: Sequence[str],
    *,
    timeout: float = 60,
) -> BackendHandle:
    """Find or start the owner; start_argv must use this folder and --no-browser.

    Exit 2 waits until the overall deadline without respawning. Discovery
    mismatches are transient; a matching-pid folder mismatch refuses at deadline.
    """

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
    process, log_path = _spawn(argv, root)
    while True:
        info, health = _observe(root)
        found = _validate(root, info, health)
        if found is not None:
            return found
        code = process.poll()
        if code is not None and code not in (0, EXIT_ALREADY_RUNNING):
            raise BackendSpawnError(
                f"The backend exited with code {code} before it was ready. "
                f"See {log_path}. Start log tail:\n{_log_tail(log_path)}"
            )
        if time.monotonic() >= deadline:
            cleanup = _terminate_child(process)
            # A same-pid backend serving another folder should be impossible for
            # the supported backend. Refuse only now: preferred-port conflicts
            # and metadata updates are normal during a legitimate startup.
            if (
                info is not None
                and health is not None
                and health.get("pid") == info.pid
                and not _same_path(health.get("data_dir"), root)
            ):
                raise BackendDataDirMismatch(
                    f"The backend pid {info.pid} on port {info.port} reports data folder "
                    f"{health.get('data_dir')!r}, not {str(root)!r}, at the startup deadline. "
                    "Stop that backend or correct its data-folder configuration; do not "
                    f"work around this with a different data folder. See {log_path}.{cleanup}"
                )
            raise BackendStartTimeout(
                "The backend did not become ready within the timeout. "
                f"{_owner_description(root)} See {log_path}.{cleanup}"
            )
        time.sleep(POLL_INTERVAL)
