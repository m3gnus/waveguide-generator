"""Authenticated clients of a detached, serial BEAT host."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
import copy
import hmac
import json
import math
from pathlib import Path
import socket
import threading
import time
from typing import Any

from . import paths, registry as r
from .cleanup import cleanup_host
from .host import CONTROL_TIMEOUT, DEFAULT_IDLE_TIMEOUT, RETIREMENT_TIMEOUT, validate_key
from .ipc import CONTROL_FRAME_BYTES, MAX_FRAME_BYTES, receive_frame, remaining_time, send_frame
from .ownership import OwnedStream, StreamOwnership
from .spawn import start_host

HEARTBEAT_TIMEOUT = 10.0


class HostError(RuntimeError):
    """The authenticated host failed or ended an incomplete exchange."""


class HostConnectionClosed(HostError):
    """An endpoint closed during admission; its record may be retiring."""


def _receive_frame(connection: socket.socket, *, cancelled: Callable[[], bool] | None = None,
                   **options: Any) -> dict | None:
    try:
        return receive_frame(connection, cancelled=cancelled, **options)
    except OSError as exc:
        if isinstance(exc, ConnectionAbortedError) or (cancelled is not None and cancelled()):
            raise HostError("BEAT host receive cancelled") from exc
        raise


def connect_client(record: r.HostRecord, directory: Path, *, timeout: float = CONTROL_TIMEOUT,
                   cancelled: Callable[[], bool] | None = None) -> socket.socket:
    """Prove both peers' identity within one control deadline."""
    r.validate_record(record, record.key, directory)
    deadline = time.monotonic() + timeout
    connection = record.endpoint.connect(remaining_time(deadline))
    try:
        hello = r.hello_message(record)
        send_frame(connection, hello)
        reply = _receive_frame(connection, deadline=deadline, cancelled=cancelled)
        if reply is None:
            raise HostConnectionClosed("BEAT host closed during hello")
        r.validate_hello(record, reply, hello["nonce"])
        nonce = reply["client_nonce"]
        message = {**r.host_key({}), "op": "authenticate", "key": record.key,
                   "key_id": record.identifier, "nonce": nonce,
                   "proof": r.auth_proof(record, nonce, "client_auth")}
        send_frame(connection, message)
        accepted = _receive_frame(connection, deadline=deadline, cancelled=cancelled)
        if accepted is None:
            raise HostConnectionClosed("BEAT host closed during client admission")
        proof = accepted.get("proof")
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
                frame = _receive_frame(self.connection, max_bytes=MAX_FRAME_BYTES,
                                       deadline=time.monotonic() + HEARTBEAT_TIMEOUT,
                                       cancelled=lambda: self.closed or self.client._closed.is_set())
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
                    send_frame(self.connection, {"op": "cancel", "retire_startup": True})
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
        request = dict(request_path) if isinstance(request_path, Mapping) else str(Path(request_path).absolute())
        message = {"op": "submit", "request": request, "operation": operation}
        if len(json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")) > CONTROL_FRAME_BYTES:
            raise HostError("BEAT submission exceeds the 1 MiB control limit; stage the request as a file")
        self.client.adopt()  # Maintain an authenticated lifetime lease.
        connection = connect_client(self.client._record, self.client.directory,
                                    cancelled=self.client._closed.is_set)
        try:
            send_frame(connection, message)
            accepted = _receive_frame(connection, deadline=time.monotonic() + CONTROL_TIMEOUT,
                                      cancelled=self.client._closed.is_set)
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
                 timeout: float = 10.0, idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
                 environment: Mapping[str, str] | None = None) -> None:
        key = validate_key(key)
        if not all(math.isfinite(value) and value > 0 for value in (timeout, idle_timeout)):
            raise ValueError("Host timeouts must be positive and finite")
        self.key = copy.deepcopy(key)
        self.environment = dict(environment) if environment is not None else None
        self.directory = paths.checked_root(paths.worker_dir() if directory is None else directory).absolute()
        self.timeout, self.idle_timeout = timeout, idle_timeout
        self.host_pid: int | None = None
        self.engine_pid: int | None = None
        self.worker_instance: str | None = None
        self.engine_replaced = False
        self._worker_info: dict | None = None
        self._record: r.HostRecord | None = None
        self._connection: socket.socket | None = None
        self._control = threading.Lock()
        self._lifetime = threading.Lock()
        self._closed = threading.Event()
        self._startup_cancelled = threading.Event()
        self._startup_done = threading.Event()
        self._startup_done.set()
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
        if ((self.host_pid is not None and self.host_pid != frame["host_pid"])
                or (self.worker_instance is not None and self.worker_instance != frame["worker_instance"])):
            self.engine_replaced = True
        self.host_pid = frame["host_pid"]
        self.worker_instance = frame["worker_instance"]
        self._worker_info = copy.deepcopy(info)

    def _connect(self) -> socket.socket:
        if self._closed.is_set():
            raise HostError("BEAT client admission is closed")
        if self._connection is None:
            options = {} if self.environment is None else {"environment": self.environment}
            deadline = time.monotonic() + self.timeout
            while True:
                if self._closed.is_set():
                    raise HostError("BEAT client admission is closed")
                self._record = start_host(self.key, self.directory, timeout=remaining_time(deadline),
                                          idle_timeout=self.idle_timeout, **options)
                try:
                    connection = connect_client(self._record, self.directory,
                                                timeout=min(CONTROL_TIMEOUT, remaining_time(deadline)),
                                                cancelled=self._closed.is_set)
                    break
                except (HostConnectionClosed, ConnectionError) as exc:
                    # A hello can race a retiring host's final admission close.
                    # Recheck under spawn exclusion; never prune a live peer or
                    # retry a wrong proof, and share the original start budget.
                    if time.monotonic() >= deadline:
                        raise HostError("BEAT host admission did not reopen") from exc
                    self._closed.wait(min(0.02, max(0, deadline - time.monotonic())))
            with self._lifetime:
                if self._closed.is_set():
                    connection.close()
                    raise HostError("BEAT client detached during connection")
                self._connection = connection
                if self.host_pid is not None and self.host_pid != self._record.pid:
                    self.engine_replaced = True
                    self._worker_info = None
                self.host_pid = self._record.pid
        return self._connection

    def _request(self, operation: str, terminal: str, *,
                 status_callback: Callable[[str], None] | None = None, streaming: bool = False,
                 timeout: float = CONTROL_TIMEOUT) -> dict:
        with self._control:
            connection = self._connect()
            deadline = time.monotonic() + timeout
            try:
                if operation == "ensure_started":
                    with self._lifetime:
                        if self._startup_cancelled.is_set():
                            raise HostError("BEAT startup cancelled")
                        send_frame(connection, {"op": operation})
                else:
                    send_frame(connection, {"op": operation})
                while True:
                    frame = _receive_frame(connection, deadline=(time.monotonic() + HEARTBEAT_TIMEOUT
                                                                  if streaming else deadline),
                                           cancelled=lambda: (self._closed.is_set()
                                                              or self._startup_cancelled.is_set()))
                    if frame is None:
                        raise HostConnectionClosed("BEAT host closed before answering")
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
        frame["engine_replaced"] = self.engine_replaced
        return frame

    def ensure_started(self, *, status_callback: Callable[[str], None] | None = None) -> None:
        self._startup_done.clear()
        try:
            for attempt in range(2):
                if self._startup_cancelled.is_set() or self._closed.is_set():
                    raise HostError("BEAT startup cancelled")
                try:
                    frame = self._request("ensure_started", "ready", status_callback=status_callback,
                                          streaming=True)
                    self._report(frame)
                    return
                except (HostConnectionClosed, ConnectionError) as exc:
                    # A successor may queue behind cancelled cold startup before
                    # its host's retirement backstop closes admission. Startup
                    # can retry safely; numerical submissions never replay.
                    if attempt:
                        raise HostError("BEAT host startup admission did not reopen") from exc
        finally:
            self._startup_done.set()

    def submit(self, request_path: Path | Mapping, *, operation: str = "solve",
               status_callback: Callable[[str], None] | None = None) -> OwnedStream:
        return self._ownership.submit(request_path, operation=operation, status_callback=status_callback)

    def ping(self) -> dict:
        return self._request("ping", "pong")

    def terminate(self) -> bool:
        """Request idle retirement; another client's queued/active work declines it."""
        return bool(self._request("retire", "retired", timeout=RETIREMENT_TIMEOUT + CONTROL_TIMEOUT)
                    .get("engine_retired"))

    def _disconnect(self) -> None:
        with self._lifetime:
            connection, self._connection = self._connection, None
        if connection is not None:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()

    def cancel_startup(self) -> None:
        """Retire only this connection's FIFO startup, with a bounded host backstop."""
        sent = False
        with self._lifetime:
            # The cancelled reader takes this lock to disconnect. Send the
            # retirement request before it can close the startup connection.
            self._startup_cancelled.set()
            if self._connection is not None:
                with contextlib.suppress(OSError):
                    send_frame(self._connection, {"op": "cancel", "retire_startup": True})
                    sent = True
        if sent and not self._startup_done.wait(RETIREMENT_TIMEOUT + CONTROL_TIMEOUT):
            self._disconnect()
            raise HostError("BEAT startup cancellation did not finish")

    def detach(self) -> None:
        """End this client's admission and streams, preserving the host lease window."""
        self._closed.set()
        try:
            self._ownership.shutdown()
        finally:
            # Closing also interrupts a concurrent ensure_started/control read.
            self._disconnect()

    def shutdown(self) -> None:
        """Explicit Quit: detach this client and shut down its authenticated host."""
        self.detach()
        if self._record is not None:
            cleanup_host(self._record, self.key, self.directory, timeout=self.timeout)
