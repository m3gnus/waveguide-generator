"""Private atomic host records and persistent cross-process spawn exclusion."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
from typing import Any, Iterator

from . import paths
from .ipc import Endpoint, validate_identifier


class RecordRefused(ValueError):
    """A foreign, malformed, unsafe or unauthenticated record must be retained."""


def host_key(identity: dict[str, Any]) -> dict[str, Any]:
    """Namespace caller-supplied launch identity; never import the engine."""
    return {**identity, "provider": paths.PROVIDER_ID, "protocol": paths.HOST_PROTOCOL,
            "protocol_version": paths.HOST_PROTOCOL_VERSION}


def _validate_scope(raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict) or any(raw.get(name) != value for name, value in (
        ("provider", paths.PROVIDER_ID), ("protocol", paths.HOST_PROTOCOL),
        ("protocol_version", paths.HOST_PROTOCOL_VERSION),
    )) or type(raw.get("protocol_version")) is not int:
        raise RecordRefused("Foreign provider or host protocol")


def key_id(key: dict[str, Any]) -> str:
    canonical = json.dumps(key, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _root(directory: Path | None) -> Path:
    return (paths.worker_dir() if directory is None else Path(directory)).absolute()


def _check_owner(info: os.stat_result) -> None:
    if os.name == "posix" and info.st_uid != os.getuid():
        raise RecordRefused("Registry path belongs to another user")


def private_directory(directory: Path | None = None) -> Path:
    root = _root(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RecordRefused("Registry root must be a directory, without symlinks")
    _check_owner(info)
    if os.name == "posix":
        root.chmod(0o700)
    return root


def record_path(identifier: str, directory: Path | None = None) -> Path:
    validate_identifier(identifier)
    return _root(directory) / f"{identifier}.json"


def spawn_lock_path(identifier: str, directory: Path | None = None) -> Path:
    return record_path(identifier, directory).with_suffix(".lock")


def launch_spec_path(identifier: str, directory: Path | None = None) -> Path:
    return record_path(identifier, directory).with_suffix(".key.json")


def log_path(identifier: str, directory: Path | None = None) -> Path:
    return record_path(identifier, directory).with_suffix(".log")


@contextmanager
def _private_file(path: Path, *, create: bool = False) -> Iterator[int]:
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RecordRefused("Unsafe registry root")
    _check_owner(info)
    if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
        raise RecordRefused("Registry directory is not owner-private")
    flags = os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
    # lstat also protects platforms without O_NOFOLLOW.
    if path.is_symlink():
        raise RecordRefused("Linked registry file")
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RecordRefused("Registry file must be regular")
        _check_owner(info)
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise RecordRefused("Registry file is not owner-private")
        yield fd
    finally:
        os.close(fd)


def _atomic_json(path: Path, raw: dict[str, Any]) -> Path:
    body = json.dumps(raw, sort_keys=True, allow_nan=False).encode("utf-8")
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


@dataclass
class HostRecord:
    key: dict[str, Any]
    pid: int
    token: str
    endpoint: Endpoint

    @property
    def identifier(self) -> str:
        return key_id(self.key)

    def as_dict(self) -> dict[str, Any]:
        return {**host_key({}), "key_id": self.identifier, "key": self.key,
                "pid": self.pid, "token": self.token, "endpoint": self.endpoint.as_dict()}


def validate_record(record: HostRecord, expected_key: dict[str, Any], directory: Path | None = None) -> None:
    _validate_scope(expected_key)
    _validate_scope(record.key)
    if record.key != expected_key or type(record.pid) is not int or record.pid <= 0:
        raise RecordRefused("Host key or PID mismatch")
    if not isinstance(record.token, str) or not record.token:
        raise RecordRefused("Missing host token")
    endpoint = Endpoint.from_dict(record.endpoint.as_dict())
    if endpoint.kind == "unix" and endpoint.path != _root(directory) / f"{record.identifier}.sock":
        raise RecordRefused("Unix endpoint is outside this host's registry slot")
    if endpoint.kind == "tcp" and endpoint.port == 0:
        raise RecordRefused("Host endpoint has not been published")


def read_record(identifier: str, directory: Path | None = None) -> HostRecord | None:
    """Missing is None; corrupt or foreign records raise and remain untouched."""
    try:
        with _private_file(record_path(identifier, directory)) as fd:
            with os.fdopen(os.dup(fd), "r", encoding="utf-8") as stream:
                raw = json.load(stream)
        _validate_scope(raw)
        record = HostRecord(raw["key"], raw["pid"], raw["token"], Endpoint.from_dict(raw["endpoint"]))
        validate_record(record, record.key, directory)
        if raw["key_id"] != identifier or record.identifier != identifier:
            raise RecordRefused("Host record identifier mismatch")
        return record
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RecordRefused(f"Host record {identifier} refused: {exc}") from exc


def write_record(record: HostRecord, directory: Path | None = None) -> Path:
    validate_record(record, record.key, directory)
    # A foreign record at this slot is never silently replaced.
    read_record(record.identifier, directory)
    return _atomic_json(record_path(record.identifier, directory), record.as_dict())


def write_launch_spec(key: dict[str, Any], directory: Path | None = None) -> Path:
    _validate_scope(key)
    read_record(key_id(key), directory)
    return _atomic_json(launch_spec_path(key_id(key), directory), key)


def new_token() -> str:
    return secrets.token_hex(32)


def pid_alive(pid: int) -> bool:
    """Query liveness conservatively; never send a termination signal."""
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Only invalid PID proves death.
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def hello_message(record: HostRecord) -> dict[str, Any]:
    return {**host_key({}), "op": "hello", "key_id": record.identifier,
            "key": record.key, "token": record.token}


def validate_hello(record: HostRecord, reply: dict[str, Any] | None) -> None:
    """The future host must echo identity and report its own PID."""
    if reply is None:
        raise RecordRefused("Host closed before authentication")
    _validate_scope(reply)
    if (reply.get("type") != "hello_ok" or reply.get("key") != record.key
            or reply.get("key_id") != record.identifier or reply.get("token") != record.token
            or type(reply.get("host_pid")) is not int or reply["host_pid"] != record.pid):
        raise RecordRefused("Host authentication or returned PID mismatch")


class SpawnLock:
    """Hold look/recheck/publish under exclusion; never unlink the lock inode."""

    def __init__(self, path: Path, timeout: float = 60.0):
        self.path, self.timeout = path, timeout

    def __enter__(self) -> SpawnLock:
        private_directory(self.path.parent)
        self._file = _private_file(self.path, create=True)
        self._fd = self._file.__enter__()
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    self._lock(False)
                    return self
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Host spawn lock timed out: {self.path}") from exc
                    time.sleep(0.02)
        except BaseException:
            self._file.__exit__(None, None, None)
            raise

    def _lock(self, release: bool) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._lock(True)
        finally:
            self._file.__exit__(None, None, None)
