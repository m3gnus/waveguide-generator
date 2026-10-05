"""WG host framing and local endpoints, separate from engine transport."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import struct
from typing import Any

from . import paths

_HEADER = struct.Struct("!I")
MAX_FRAME_BYTES = 512 * 1024 * 1024
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


def _receive_exactly(connection: socket.socket, count: int, *, eof_ok: bool = False) -> bytes | None:
    chunks = []
    remaining = count
    while remaining:
        chunk = connection.recv(min(remaining, 1 << 20))
        if not chunk:
            if eof_ok and remaining == count:
                return None
            raise FrameError("Truncated host frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _invalid_constant(value: str) -> None:
    raise FrameError(f"Invalid JSON constant: {value}")


def receive_frame(connection: socket.socket) -> dict[str, Any] | None:
    """Return None only for clean EOF between frames."""
    header = _receive_exactly(connection, _HEADER.size, eof_ok=True)
    if header is None:
        return None
    length, = _HEADER.unpack(header)
    if not 0 < length <= MAX_FRAME_BYTES:
        raise FrameError("Invalid host frame length")
    body = _receive_exactly(connection, length)
    try:
        decoded = json.loads(body.decode("utf-8"), parse_constant=_invalid_constant)
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
        return socket.socket(socket.AF_UNIX if self.kind == "unix" else socket.AF_INET, socket.SOCK_STREAM)

    def _address(self) -> str | tuple[str, int]:
        return str(self.path) if self.kind == "unix" else ("127.0.0.1", self.port)

    def listen(self) -> socket.socket:
        """Bind without removing existing endpoints or enabling address reuse."""
        server = self._socket()
        try:
            server.bind(self._address())
            if self.kind == "unix" and os.name == "posix":
                os.chmod(self.path, 0o600)
            server.listen(16)
            if self.kind == "tcp":
                self.port = server.getsockname()[1]
        except OSError:
            server.close()
            raise
        return server

    def connect(self, timeout: float) -> socket.socket:
        if self.kind == "tcp" and self.port == 0:
            raise ValueError("Unpublished TCP endpoint")
        client = self._socket()
        try:
            client.settimeout(timeout)
            client.connect(self._address())
        except OSError:
            client.close()
            raise
        return client


def endpoint_for(identifier: str, directory: Path | None = None, *, transport: str | None = None) -> Endpoint:
    """Prefer Unix when its encoded path fits; otherwise bind loopback TCP."""
    validate_identifier(identifier)
    if transport not in {None, "unix", "tcp"}:
        raise ValueError("Unknown host transport")
    path = (paths.worker_dir() if directory is None else directory).absolute() / f"{identifier}.sock"
    available = os.name == "posix" and hasattr(socket, "AF_UNIX")
    if transport == "unix" and not available:
        raise ValueError("Unix sockets unavailable")
    if transport != "tcp" and available and len(os.fsencode(path)) <= MAX_UNIX_PATH_BYTES:
        return Endpoint("unix", path=path)
    return Endpoint("tcp")
