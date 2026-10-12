"""The file-only retirement channel shared by a GUI and its adopted backend."""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import stat
import tempfile
import time

REQUEST_FILENAME = "backend-retire.json"
MAX_REQUEST_BYTES = 4096
log = logging.getLogger("wg.launch")


def _open_request(path: Path) -> int:
    if os.name != "nt":
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    # O_NOFOLLOW is unavailable on Windows. Open the reparse point itself so
    # even a symlink substituted after lstat is never followed.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def write_request(locks_dir, *, pid, mode, reason, now=time.time):
    """Publish atomically; errors belong to the caller, including fsync errors."""
    directory = Path(locks_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"schema": 1, "pid": pid, "mode": mode, "reason": reason, "requested_at": float(now())}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / REQUEST_FILENAME)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def take_request(locks_dir, *, pid, started_at_wall, now=time.time):
    """Consume even invalid files, without reading symlinks or oversized files.

    Counterparts: statusapp/controller.py writes; launch/backend_lifetime.py takes.
    An undeletable file is not acted on, so a Windows sharing violation retries.
    """
    path = Path(locks_dir) / REQUEST_FILENAME
    ignored = None
    raw = None
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        metadata = None
        ignored = "it could not be inspected"
    try:
        if metadata is None:
            pass
        elif stat.S_ISLNK(metadata.st_mode):
            ignored = "it is a symlink"
        elif not stat.S_ISREG(metadata.st_mode):
            ignored = "it is not a regular file"
        elif metadata.st_size > MAX_REQUEST_BYTES:
            ignored = "it exceeds 4 KiB"
        else:
            descriptor = _open_request(path)
            with os.fdopen(descriptor, "rb") as stream:
                opened, after = os.fstat(stream.fileno()), path.lstat()
                if (
                    not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(after.st_mode)
                    or not os.path.samestat(metadata, opened) or not os.path.samestat(after, opened)
                ):
                    ignored = "it changed while being read"
                else:
                    raw = stream.read(MAX_REQUEST_BYTES + 1)
                    if len(raw) > MAX_REQUEST_BYTES:
                        ignored = "it exceeds 4 KiB"
    except OSError:
        ignored = "it could not be read"
    try:
        path.unlink()
    except OSError:
        return None
    if ignored is None:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                ignored = "it is not a JSON object"
            elif type(request.get("schema")) is not int or request["schema"] != 1:
                ignored = "unknown schema"
            elif type(request.get("pid")) is not int or request["pid"] != pid:
                ignored = "pid does not match"
            elif request.get("mode") not in ("release", "retire"):
                ignored = "unknown mode"
            elif not isinstance(request.get("reason"), str) or not 0 < len(request["reason"]) <= 128:
                ignored = "invalid reason"
            else:
                stamp = request.get("requested_at")
                if type(stamp) not in (int, float) or not math.isfinite(stamp):
                    ignored = "invalid requested_at"
                elif stamp < started_at_wall - 2:
                    ignored = "request predates this backend"
                else:
                    request["requested_at"] = min(float(stamp), now())
                    return request
        except (ValueError, TypeError, UnicodeError, OverflowError):
            ignored = "malformed request"
    log.info("Backend retirement request ignored (%s)", ignored)
    return None
