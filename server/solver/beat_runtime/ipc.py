"""WG host framing and local endpoints, separate from engine transport."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import errno
import json
import math

import numpy as np
import os
from pathlib import Path
import re
import socket
import struct
import time
from typing import Any

from . import paths

_HEADER = struct.Struct("!I")
MAX_FRAME_BYTES = 512 * 1024 * 1024
CONTROL_FRAME_BYTES = 1024 * 1024
MAX_UNIX_PATH_BYTES = 100


class FrameError(ValueError):
    """A peer sent a malformed, oversized or truncated frame."""


def send_frame(connection: socket.socket, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise FrameError("Host frame must be a JSON object")
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise FrameError("Host frame exceeds the size limit")
    connection.sendall(_HEADER.pack(len(body)) + body)


def remaining_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Host control deadline exceeded")
    return remaining


def _receive_exactly(
    connection: socket.socket, count: int, *, eof_ok: bool = False, deadline: float | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> bytes | None:
    chunks = []
    remaining = count
    while remaining:
        if cancelled is not None and cancelled():
            raise ConnectionAbortedError("BEAT host receive cancelled")
        if deadline is not None:
            budget = remaining_time(deadline)
            connection.settimeout(min(budget, 0.05) if cancelled is not None else budget)
        elif cancelled is not None:
            connection.settimeout(0.05)
        try:
            chunk = connection.recv(min(remaining, 1 << 20))
        except TimeoutError:
            if cancelled is None:
                raise
            # Preserve partial headers/bodies while checking cancellation and
            # the original deadline; closing a socket need not wake its reader.
            continue
        if deadline is not None:
            remaining_time(deadline)
        if not chunk:
            if eof_ok and remaining == count:
                return None
            raise FrameError("Truncated host frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _invalid_constant(value: str) -> None:
    raise FrameError(f"Invalid JSON constant: {value}")


def _check_finite(value: Any) -> None:
    """Walk JSON containers; vectorize only long, homogeneous numeric lists."""
    if isinstance(value, dict):
        for member in value.values():
            _check_finite(member)
    elif isinstance(value, list):
        if len(value) > 64 and all(isinstance(member, (int, float)) for member in value):
            try:
                array = np.asarray(value)
            except (OverflowError, ValueError):
                array = None
            if array is not None and array.dtype.kind in "biuf":
                if not np.isfinite(array).all():
                    raise FrameError("Nonfinite JSON number")
                return
        for member in value:
            _check_finite(member)
    elif isinstance(value, float) and not math.isfinite(value):
        raise FrameError("Nonfinite JSON number")


def receive_frame(
    connection: socket.socket, *, max_bytes: int = CONTROL_FRAME_BYTES, deadline: float | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Bound allocation and total receive time; None means clean EOF between frames.

    Numerical stream callers may explicitly opt into MAX_FRAME_BYTES. Control
    callers pass one monotonic deadline across all frames in their exchange.
    """
    if not 0 < max_bytes <= MAX_FRAME_BYTES:
        raise ValueError("Invalid host frame size limit")
    header = _receive_exactly(connection, _HEADER.size, eof_ok=True, deadline=deadline,
                              cancelled=cancelled)
    if header is None:
        return None
    length, = _HEADER.unpack(header)
    if not 0 < length <= max_bytes:
        raise FrameError("Invalid host frame length")
    body = _receive_exactly(connection, length, deadline=deadline, cancelled=cancelled)
    try:
        decoded = json.loads(body.decode("utf-8"), parse_constant=_invalid_constant)
        _check_finite(decoded)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise FrameError("Malformed host JSON frame") from exc
    if not isinstance(decoded, dict):
        raise FrameError("Host frame must be a JSON object")
    return decoded


def validate_identifier(identifier: str) -> None:
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{16}", identifier):
        raise ValueError("Invalid host key identifier")


@dataclass
class Endpoint:
    """A Unix socket path or an IPv4 loopback port; TCP zero is bind-only."""

    kind: str
    path: Path | None = None
    port: int = 0

    def __post_init__(self) -> None:
        if self.kind == "unix" and self.path is not None and self.port == 0:
            self.path = Path(self.path)
            if not self.path.is_absolute() or "\0" in str(self.path):
                raise ValueError("Unix endpoint must be an absolute local path")
        elif self.kind != "tcp" or self.path is not None or type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("Invalid host endpoint")

    def as_dict(self) -> dict[str, Any]:
        if self.kind == "unix":
            return {"kind": self.kind, "path": str(self.path)}
        return {"kind": self.kind, "host": "127.0.0.1", "port": self.port}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Endpoint:
        if not isinstance(raw, dict):
            raise ValueError("Invalid host endpoint")
        if raw.get("kind") == "unix" and isinstance(raw.get("path"), str):
            return cls("unix", path=Path(raw["path"]))
        if raw.get("kind") == "tcp" and raw.get("host") == "127.0.0.1":
            return cls("tcp", port=raw.get("port"))
        raise ValueError("Host endpoint must be local")

    def _socket(self) -> socket.socket:
        if self.kind == "unix" and (os.name == "nt" or not hasattr(socket, "AF_UNIX")):
            raise ValueError("Unix sockets unavailable")
        return socket.socket(socket.AF_UNIX if self.kind == "unix" else socket.AF_INET, socket.SOCK_STREAM)

    def _address(self) -> str | tuple[str, int]:
        return str(self.path) if self.kind == "unix" else ("127.0.0.1", self.port)

    def listen(self) -> socket.socket:
        """Bind without removing existing endpoints or enabling address reuse."""
        if self.kind == "unix":
            paths.checked_root(self.path.parent)
        server = self._socket()
        bound = False
        try:
            if self.kind == "tcp" and os.name == "nt":
                server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            server.bind(self._address())
            bound = True
            if self.kind == "unix" and os.name == "posix":
                os.chmod(self.path, 0o600)
            server.listen(16)
            if self.kind == "tcp":
                self.port = server.getsockname()[1]
        except BaseException:
            server.close()
            if bound and self.kind == "unix":
                self.path.unlink(missing_ok=True)
            raise
        return server

    def connect(self, timeout: float) -> socket.socket:
        if self.kind == "tcp" and self.port == 0:
            raise ValueError("Unpublished TCP endpoint")
        client = self._socket()
        try:
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Host connection deadline expired")
                client.settimeout(remaining)
                try:
                    client.connect(self._address())
                    break
                except BlockingIOError as exc:
                    # Linux AF_UNIX returns EAGAIN immediately when the accept
                    # queue is full, even with a socket timeout. No connection
                    # has started; retry within the caller's original budget.
                    if self.kind != "unix" or exc.errno != errno.EAGAIN:
                        raise
                    time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        except BaseException:
            client.close()
            raise
        return client


def configure_stream_socket(connection: socket.socket, *, tcp: bool = False) -> None:
    """Reduce relay backpressure; platform buffer caps need not accept 4 MiB."""
    options = [(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024),
               (socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)]
    if tcp:
        options.append((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1))
    for level, option, value in options:
        try:
            connection.setsockopt(level, option, value)
        except OSError:
            pass


def endpoint_for(identifier: str, directory: Path | None = None, *, transport: str | None = None) -> Endpoint:
    """Prefer Unix when its encoded path fits; otherwise bind loopback TCP."""
    validate_identifier(identifier)
    if transport not in {None, "unix", "tcp"}:
        raise ValueError("Unknown host transport")
    root = paths.worker_dir() if directory is None else paths.checked_root(directory)
    path = root.absolute() / f"{identifier}.sock"
    available = os.name == "posix" and hasattr(socket, "AF_UNIX")
    if transport == "unix" and not available:
        raise ValueError("Unix sockets unavailable")
    if transport != "tcp" and available and len(os.fsencode(path)) <= MAX_UNIX_PATH_BYTES:
        return Endpoint("unix", path=path)
    return Endpoint("tcp")
