"""Authenticated clients of a detached, serial BEAT host."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
import copy
import hmac
import math
from pathlib import Path
import socket
import threading
import time
from typing import Any

from . import paths, registry as r
from .cleanup import cleanup_host
from .host import CONTROL_TIMEOUT, DEFAULT_IDLE_TIMEOUT, validate_key
from .ipc import MAX_FRAME_BYTES, receive_frame, remaining_time, send_frame
from .ownership import OwnedStream, StreamOwnership
from .spawn import start_host

HEARTBEAT_TIMEOUT = 10.0


class HostError(RuntimeError):
    """The authenticated host failed or ended an incomplete exchange."""


def connect_client(record: r.HostRecord, directory: Path, *, timeout: float = CONTROL_TIMEOUT) -> socket.socket:
    """Prove both peers' identity within one control deadline."""
    r.validate_record(record, record.key, directory)
    deadline = time.monotonic() + timeout
    connection = record.endpoint.connect(remaining_time(deadline))
    try:
        hello = r.hello_message(record)
        send_frame(connection, hello)
        reply = receive_frame(connection, deadline=deadline)
        r.validate_hello(record, reply, hello["nonce"])
        nonce = reply["client_nonce"]
        message = {**r.host_key({}), "op": "authenticate", "key": record.key,
                   "key_id": record.identifier, "nonce": nonce,
                   "proof": r.auth_proof(record, nonce, "client_auth")}
        send_frame(connection, message)
        accepted = receive_frame(connection, deadline=deadline)
        proof = accepted.get("proof") if accepted else None
        if (not accepted or accepted.get("type") != "authenticated" or accepted.get("nonce") != nonce
                or not isinstance(proof, str)
                or not hmac.compare_digest(proof.encode(), r.auth_proof(record, nonce, "client_auth_ok").encode())):
            raise r.RecordRefused("Host client-admission proof mismatch")
        connection.settimeout(CONTROL_TIMEOUT)
        return connection
    except BaseException:
        connection.close()
        raise


class _RemoteStream:
    def __init__(self, client: HostedWorker, connection: socket.socket,
                 status_callback: Callable[[str], None] | None) -> None:
        self.client, self.connection, self.status_callback = client, connection, status_callback
        self.closed = False
        self.terminal = False

    def __next__(self) -> dict:
        if self.closed:
            raise StopIteration
        try:
            while True:
                # Each heartbeat/progress frame renews liveness; solve duration
                # has no fixed deadline. Allocation stays bounded by IPC policy.
                frame = receive_frame(self.connection, max_bytes=MAX_FRAME_BYTES,
                                      deadline=time.monotonic() + HEARTBEAT_TIMEOUT)
                if frame is None:
                    raise HostError("BEAT host disconnected before job completion")
                kind = frame.get("type")
                if kind == "worker_info":
                    self.client._report(frame)
                elif kind == "status":
                    if self.status_callback is not None:
                        self.status_callback(str(frame.get("message", "")))
                elif kind == "event":
                    event = frame.get("event")
                    if not isinstance(event, dict):
                        raise HostError("Invalid BEAT event envelope")
                    self.terminal = event.get("type") in {"completed", "cancelled", "failed"}
                    return event
                elif kind in {"failed", "cancelled"}:
                    self.terminal = True
                    return frame
                elif kind not in {"heartbeat", "queued"}:
                    raise HostError(f"Unexpected BEAT stream frame: {kind}")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if not self.terminal:
                # This connection carries exactly one submission. Its cancel
                # can never target a later stream or another client's work.
                with contextlib.suppress(OSError):
                    send_frame(self.connection, {"op": "cancel"})
        finally:
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()


class _RemoteSubmitter:
    def __init__(self, client: HostedWorker) -> None:
        self.client = client

    def submit(self, request_path: Path | Mapping, *,
               status_callback: Callable[[str], None] | None = None,
               operation: str = "solve") -> _RemoteStream:
        self.client.adopt()  # Maintain an authenticated lifetime lease.
        connection = connect_client(self.client._record, self.client.directory)
        try:
            request = dict(request_path) if isinstance(request_path, Mapping) else str(Path(request_path).absolute())
            send_frame(connection, {"op": "submit", "request": request, "operation": operation})
            accepted = receive_frame(connection, deadline=time.monotonic() + CONTROL_TIMEOUT)
            if accepted is None or accepted.get("type") != "queued":
                raise HostError(str(accepted.get("error", "BEAT submission refused"))
                                if accepted else "BEAT host closed before admission")
        except BaseException:
            connection.close()
            raise
        return _RemoteStream(self.client, connection, status_callback)


class HostedWorker:
    """Own a client lease and token-checked streams; leave Julia to the host.

    worker_instance proves host-owned EngineWorker object continuity only.
    engine_pid is unavailable in the official public API and remains None.
    """

    def __init__(self, key: dict[str, Any], *, directory: Path | None = None,
                 timeout: float = 10.0, idle_timeout: float = DEFAULT_IDLE_TIMEOUT) -> None:
        validate_key(key)
        if not all(math.isfinite(value) and value > 0 for value in (timeout, idle_timeout)):
            raise ValueError("Host timeouts must be positive and finite")
        self.key = copy.deepcopy(key)
        self.directory = paths.checked_root(paths.worker_dir() if directory is None else directory).absolute()
        self.timeout, self.idle_timeout = timeout, idle_timeout
        self.host_pid: int | None = None
        self.engine_pid: int | None = None
        self.worker_instance: str | None = None
        self._worker_info: dict | None = None
        self._record: r.HostRecord | None = None
        self._connection: socket.socket | None = None
        self._control = threading.Lock()
        self._lifetime = threading.Lock()
        self._closed = threading.Event()
        self._ownership = StreamOwnership(_RemoteSubmitter(self))

    @property
    def worker_info(self) -> dict | None:
        return copy.deepcopy(self._worker_info)

    def _report(self, frame: dict) -> None:
        if type(frame.get("host_pid")) is not int or frame["host_pid"] != self._record.pid:
            raise HostError("Host identity changed during stream")
        if not isinstance(frame.get("worker_instance"), str):
            raise HostError("Host omitted its worker identity")
        info = frame.get("worker_info")
        if info is not None and not isinstance(info, dict):
            raise HostError("Invalid worker negotiation metadata")
        self.host_pid = frame["host_pid"]
        self.worker_instance = frame["worker_instance"]
        self._worker_info = copy.deepcopy(info)

    def _connect(self) -> socket.socket:
        if self._closed.is_set():
            raise HostError("BEAT client admission is closed")
        if self._connection is None:
            self._record = start_host(self.key, self.directory, timeout=self.timeout, idle_timeout=self.idle_timeout)
            connection = connect_client(self._record, self.directory)
            with self._lifetime:
                if self._closed.is_set():
                    connection.close()
                    raise HostError("BEAT client detached during connection")
                self._connection = connection
                self.host_pid = self._record.pid
        return self._connection

    def _request(self, operation: str, terminal: str, *,
                 status_callback: Callable[[str], None] | None = None, streaming: bool = False) -> dict:
        with self._control:
            connection = self._connect()
            deadline = time.monotonic() + CONTROL_TIMEOUT
            try:
                send_frame(connection, {"op": operation})
                while True:
                    frame = receive_frame(connection, deadline=(time.monotonic() + HEARTBEAT_TIMEOUT
                                                                 if streaming else deadline))
                    if frame is None:
                        raise HostError("BEAT host closed before answering")
                    kind = frame.get("type")
                    if kind == terminal:
                        return frame
                    if kind == "failed":
                        raise HostError(str(frame.get("error", "BEAT host operation failed")))
                    if streaming and kind in {"queued", "heartbeat", "status"}:
                        if kind == "status" and status_callback is not None:
                            status_callback(str(frame.get("message", "")))
                        continue
                    raise HostError(f"Unexpected BEAT control frame: {kind}")
            except BaseException:
                self._disconnect()
                raise

    def adopt(self) -> dict:
        frame = self._request("adopt", "adopted")
        self._report(frame)
        return frame

    def ensure_started(self, *, status_callback: Callable[[str], None] | None = None) -> None:
        self._report(self._request("ensure_started", "ready", status_callback=status_callback, streaming=True))

    def submit(self, request_path: Path | Mapping, *, operation: str = "solve",
               status_callback: Callable[[str], None] | None = None) -> OwnedStream:
        return self._ownership.submit(request_path, operation=operation, status_callback=status_callback)

    def ping(self) -> dict:
        return self._request("ping", "pong")

    def terminate(self) -> bool:
        """Request idle retirement; another client's queued/active work declines it."""
        return bool(self._request("retire", "retired").get("engine_retired"))

    def _disconnect(self) -> None:
        with self._lifetime:
            connection, self._connection = self._connection, None
        if connection is not None:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()

    def detach(self) -> None:
        """End this client's admission and streams, preserving the host lease window."""
        self._closed.set()
        try:
            self._ownership.shutdown()
        finally:
            # Closing also interrupts a concurrent ensure_started/control read.
            self._disconnect()

    def shutdown(self) -> None:
        self.detach()
        if self._record is not None:
            cleanup_host(self._record, self.key, self.directory, timeout=self.timeout)
