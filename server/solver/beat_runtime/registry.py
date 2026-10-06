"""Private atomic host records and persistent cross-process spawn exclusion."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Any, Callable, Iterator

from . import paths
from .ipc import Endpoint, validate_identifier

if TYPE_CHECKING:
    from .windows_security import WindowsSecurity


class RecordRefused(ValueError):
    """A foreign, malformed, unsafe or unauthenticated record must be retained."""


class LockBusy(RecordRefused):
    """Spawn exclusion was not acquired within the requested timeout."""


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
    return (paths.worker_dir() if directory is None else paths.checked_root(directory)).absolute()


def _windows_security() -> WindowsSecurity:
    from .windows_security import WindowsSecurity

    return WindowsSecurity()


def _check_owner(info: os.stat_result) -> None:
    if os.name == "posix" and info.st_uid != os.getuid():
        raise RecordRefused("Registry path belongs to another user")


def _check_path(path: Path, info: os.stat_result) -> None:
    _check_owner(info)
    if os.name == "nt":
        if getattr(info, "st_file_attributes", 0) & 0x400 or getattr(path, "is_junction", lambda: False)():
            raise RecordRefused("Registry reparse point refused")
        try:
            _windows_security().check_path(path)
        except (OSError, ValueError) as exc:
            raise RecordRefused(f"Unsafe Windows registry path: {exc}") from exc


def private_directory(directory: Path | None = None) -> Path:
    root = _root(directory)
    if os.name == "nt":
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            _windows_security().create_directory(root)
        except (OSError, ValueError) as exc:
            raise RecordRefused(f"Cannot create private Windows registry: {exc}") from exc
    else:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RecordRefused("Registry root must be a directory, without symlinks")
    _check_path(root, info)
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


def _windows_append_fd(path: Path, *, create: bool) -> int:
    """Open an append-only OS handle; caller applies the private-file checks.

    CRT O_APPEND only seeks before that descriptor's writes. Subprocesses
    inherit the OS handle without the CRT flag, so FILE_WRITE_DATA must be
    absent to make their stdout/stderr append too. This fd cannot truncate.
    """
    import ctypes
    from ctypes import wintypes as w
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                  w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
    handle = kernel.CreateFileW(
        # FILE_READ_ATTRIBUTES allows _private_file's fstat validation on Python
        # 3.13 without granting FILE_WRITE_DATA (or the ability to overwrite).
        str(path), 0x00000004 | 0x00100000 | 0x80,  # FILE_APPEND_DATA | SYNCHRONIZE | FILE_READ_ATTRIBUTES
        0x1 | 0x2 | 0x4,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        None, 4 if create else 3,  # OPEN_ALWAYS / OPEN_EXISTING
        0x80 | 0x00200000, None,  # FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_APPEND)
        if fd == -1:
            raise OSError(errno.EBADF, "Cannot wrap Windows append handle")
        return fd  # Ownership transfers to the CRT; os.close now closes the handle.
    except BaseException:
        kernel.CloseHandle(handle)
        raise


@contextmanager
def _private_file(path: Path, *, create: bool = False, append: bool = False) -> Iterator[int]:
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RecordRefused("Unsafe registry root")
    _check_path(path.parent, info)
    if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
        raise RecordRefused("Registry directory is not owner-private")
    flags = os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
    if append:
        flags |= os.O_APPEND
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
    else:
        if stat.S_ISLNK(info.st_mode):
            raise RecordRefused("Linked registry file")
        if not stat.S_ISREG(info.st_mode):
            raise RecordRefused("Registry file must be regular")
        _check_path(path, info)
    if os.name == "nt" and append:
        fd = _windows_append_fd(path, create=create)
    else:
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RecordRefused("Registry file must be regular")
        _check_path(path, info)
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise RecordRefused("Registry file is not owner-private")
        yield fd
    finally:
        os.close(fd)


def _retry_permission(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Windows scanners may briefly hold a record open; bound the retry."""
    for attempt in range(4):
        try:
            return operation(*args, **kwargs)
        except PermissionError:
            if os.name != "nt" or attempt == 3:
                raise
            time.sleep(0.01)


def unlink_record(path: Path) -> None:
    paths.checked_root(path.parent)
    _retry_permission(path.unlink, missing_ok=True)


def _atomic_json(path: Path, raw: dict[str, Any]) -> Path:
    body = json.dumps(raw, sort_keys=True, allow_nan=False).encode("utf-8")
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            _check_path(Path(temporary), Path(temporary).lstat())
        _retry_permission(os.replace, temporary, path)
    finally:
        unlink_record(Path(temporary))
    return path


@dataclass
class HostRecord:
    key: dict[str, Any]
    pid: int
    token: str
    endpoint: Endpoint
    pid_start: str | None = None

    def __post_init__(self) -> None:
        if self.pid_start is None:
            self.pid_start = process_start_identity(self.pid)

    @property
    def identifier(self) -> str:
        return key_id(self.key)

    def as_dict(self) -> dict[str, Any]:
        return {**host_key({}), "key_id": self.identifier, "key": self.key,
                "pid": self.pid, "pid_start": self.pid_start, "token": self.token,
                "endpoint": self.endpoint.as_dict()}


def validate_record(record: HostRecord, expected_key: dict[str, Any], directory: Path | None = None) -> None:
    root = _root(directory)
    _validate_scope(expected_key)
    _validate_scope(record.key)
    if record.key != expected_key or type(record.pid) is not int or not 1 <= record.pid <= 2**31 - 1:
        raise RecordRefused("Host key or PID mismatch")
    if not isinstance(record.pid_start, str) or not record.pid_start.strip():
        raise RecordRefused("Missing host process start identity")
    if not isinstance(record.token, str) or not record.token:
        raise RecordRefused("Missing host token")
    endpoint = Endpoint.from_dict(record.endpoint.as_dict())
    if endpoint.kind == "unix" and os.name == "nt":
        raise RecordRefused("Unix host record unavailable on Windows")
    if endpoint.kind == "unix" and endpoint.path != root / f"{record.identifier}.sock":
        raise RecordRefused("Unix endpoint is outside this host's registry slot")
    if endpoint.kind == "tcp" and endpoint.port == 0:
        raise RecordRefused("Host endpoint has not been published")


def read_record(identifier: str, directory: Path | None = None) -> HostRecord | None:
    """Missing is None; corrupt or foreign records raise and remain untouched."""
    path = record_path(identifier, directory)
    try:
        with _private_file(path) as fd:
            with os.fdopen(os.dup(fd), "r", encoding="utf-8") as stream:
                raw = json.load(stream)
        _validate_scope(raw)
        if not isinstance(raw.get("pid_start"), str) or not raw["pid_start"].strip():
            raise RecordRefused("Missing host process start identity")
        record = HostRecord(raw["key"], raw["pid"], raw["token"], Endpoint.from_dict(raw["endpoint"]), raw["pid_start"])
        validate_record(record, record.key, directory)
        if raw["key_id"] != identifier or record.identifier != identifier:
            raise RecordRefused("Host record identifier mismatch")
        return record
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as exc:
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


def _posix_process(pid: int) -> tuple[str, str] | None:
    try:
        if sys.platform.startswith("linux"):
            # comm can contain spaces and parentheses; fields follow its last ')'.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return fields[0], f"linux:{boot}:{fields[19]}"
        result = subprocess.run(["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=0.5,
                                env={**os.environ, "LC_ALL": "C"})
        fields = result.stdout.strip().split(None, 1)
        if result.returncode == 0 and len(fields) == 2:
            return fields[0], f"posix:{fields[1]}"
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return None


def process_start_identity(pid: int) -> str | None:
    """Return a process creation identity, or None when it cannot be verified."""
    if type(pid) is not int or not 1 <= pid <= 2**31 - 1:
        return None
    if os.name != "nt":
        process = _posix_process(pid)
        return process[1] if process is not None else None
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            created = times[0]
            return f"windows:{(created.dwHighDateTime << 32) | created.dwLowDateTime}"
        return None
    finally:
        kernel.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    """Query liveness conservatively; never send a termination signal."""
    if type(pid) is not int or not 1 <= pid <= 2**31 - 1:
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
    process = _posix_process(pid)
    if process is not None and process[0].startswith("Z"):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def auth_proof(record: HostRecord, nonce: str, operation: str) -> str:
    """HMAC over nonce || key_id || host_pid || protocol, domain-separated by op.

    Nonce and key ID are fixed-length ASCII hex; PID is decimal; protocol is
    'wg-beat-host:1:<operation>'. The secret is UTF-8, never a wire field.
    """
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise RecordRefused("Invalid authentication nonce")
    message = f"{nonce}{record.identifier}{record.pid}{paths.HOST_PROTOCOL}:{paths.HOST_PROTOCOL_VERSION}:{operation}"
    return hmac.new(record.token.encode(), message.encode(), hashlib.sha256).hexdigest()


def hello_message(record: HostRecord, *, operation: str = "hello") -> dict[str, Any]:
    if operation not in {"hello", "shutdown"}:
        raise ValueError("Unknown control operation")
    nonce = secrets.token_hex(32)
    message = {**host_key({}), "op": operation, "key_id": record.identifier,
               "key": record.key, "nonce": nonce}
    if operation == "shutdown":
        message["proof"] = auth_proof(record, nonce, "shutdown_request")
    return message


def auth_reply(record: HostRecord, request: dict[str, Any]) -> dict[str, Any]:
    """Future hosts use this after checking their own record and request scope."""
    _validate_scope(request)
    operation = request.get("op")
    if (not isinstance(operation, str) or operation not in {"hello", "shutdown"}
            or request.get("key") != record.key or request.get("key_id") != record.identifier):
        raise RecordRefused("Control request identity mismatch")
    nonce = request.get("nonce")
    proof = auth_proof(record, nonce, operation)
    if operation == "shutdown":
        supplied = request.get("proof")
        expected = auth_proof(record, nonce, "shutdown_request")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise RecordRefused("Unauthenticated shutdown request")
    return {**host_key({}), "type": f"{operation}_ok", "key": record.key,
            "key_id": record.identifier, "host_pid": record.pid, "nonce": nonce, "proof": proof}


def validate_hello(
    record: HostRecord, reply: dict[str, Any] | None, nonce: str, *, operation: str = "hello",
) -> None:
    """Authenticate a fresh host proof, including its returned PID."""
    if reply is None:
        raise RecordRefused("Host closed before authentication")
    _validate_scope(reply)
    if (reply.get("type") != f"{operation}_ok" or reply.get("key") != record.key
            or reply.get("key_id") != record.identifier or reply.get("nonce") != nonce
            or type(reply.get("host_pid")) is not int or reply["host_pid"] != record.pid):
        raise RecordRefused("Host authentication or returned PID mismatch")
    proof = reply.get("proof")
    if not isinstance(proof, str) or not hmac.compare_digest(proof.encode(), auth_proof(record, nonce, operation).encode()):
        raise RecordRefused("Host authentication proof mismatch")


class SpawnLock:
    """Hold look/recheck/publish under exclusion; never unlink the lock inode."""

    def __init__(self, path: Path, timeout: float = 60.0):
        self.path, self.timeout = path, timeout
        self.held = False

    def __enter__(self) -> SpawnLock:
        private_directory(self.path.parent)
        self._file = _private_file(self.path, create=True)
        self._fd = self._file.__enter__()
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    self._lock(False)
                    self.held = True
                    return self
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise LockBusy(f"Host spawn lock timed out: {self.path}") from exc
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
            self.held = False
            self._file.__exit__(None, None, None)
