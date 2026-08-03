"""Single-instance coordination and local server port selection."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import json
import logging
import os
from pathlib import Path
import socket
from typing import Mapping


DEFAULT_PORT = 3100
PORT_ENV = "WG2_PORT"
PORT_SCAN_COUNT = 9
LOCK_FILENAME = "server.pid"

log = logging.getLogger("wg2.instance")


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
        super().__init__(
            f"Waveguide Generator v2 is already running (pid {info.pid}, "
            f"port {info.port}; lock {path}). Close that instance or use it at "
            f"http://127.0.0.1:{info.port}/."
        )


# A concise alias is convenient for callers and older launcher prototypes.
LockConflict = InstanceAlreadyRunning


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def read_lock_info(path: Path) -> InstanceInfo | None:
    """Read lock metadata; malformed lock files are treated as stale."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return InstanceInfo(pid=int(payload["pid"]), port=int(payload["port"]))
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


class InstanceLock:
    """An atomic pid-file lock with dead-process staleness detection."""

    def __init__(self, locks_dir: str | os.PathLike[str]):
        self.path = Path(locks_dir) / LOCK_FILENAME
        self._owned = False
        self._pid = os.getpid()

    @property
    def owned(self) -> bool:
        return self._owned

    def acquire(self, port: int) -> InstanceInfo:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        info = InstanceInfo(pid=self._pid, port=port)
        payload = json.dumps({"pid": info.pid, "port": info.port}, sort_keys=True) + "\n"

        for _attempt in range(3):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                owner = read_lock_info(self.path)
                if owner is not None and _pid_is_running(owner.pid):
                    raise InstanceAlreadyRunning(owner, self.path) from None
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise InstanceLockError(
                        f"The stale instance lock at {self.path} could not be removed: "
                        f"{exc}. Remove that file manually, then start again."
                    ) from exc
                log.warning("Removed stale instance lock %s; starting a new instance", self.path)
                continue
            except OSError as exc:
                raise InstanceLockError(
                    f"Could not create instance lock {self.path}: {exc}. Check that "
                    "the data directory is writable, then start again."
                ) from exc
            else:
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                except BaseException:
                    self.path.unlink(missing_ok=True)
                    raise
                self._owned = True
                return info

        raise InstanceLockError(
            f"Instance lock {self.path} kept changing while startup was in progress. "
            "Wait a moment and start Waveguide Generator v2 again."
        )

    def release(self) -> None:
        if not self._owned:
            return
        owner = read_lock_info(self.path)
        if owner is not None and owner.pid == self._pid:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self._owned = False

    def __enter__(self) -> "InstanceLock":
        if not self._owned:
            raise RuntimeError("Call InstanceLock.acquire(port) before entering its context")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def requested_port(
    cli_port: int | None = None, *, environ: Mapping[str, str] | None = None
) -> int:
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


def port_is_available(port: int, host: str = "127.0.0.1") -> bool:
    """Probe whether a local TCP port can currently be bound."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def acquire_port(
    cli_port: int | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    host: str = "127.0.0.1",
) -> int:
    """Resolve configuration and select an available local port."""

    return select_port(requested_port(cli_port, environ=environ), host=host)
