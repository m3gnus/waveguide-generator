"""Detached owner of one optional official BEAT EngineWorker."""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable
import contextlib
import hmac
import importlib
import json
import math
import os
from pathlib import Path
import socket
import select
import signal
import threading
import time
from typing import Any

from server.platform.paths import app_root

from . import paths, registry as r
from .cleanup import sweep_orphan_socket
from .ipc import endpoint_for, receive_frame, send_frame
from .ownership import OwnedStream, StreamOwnership

DEFAULT_IDLE_TIMEOUT = 1800.0
CONTROL_TIMEOUT = 2.0
HEARTBEAT_INTERVAL = 0.5
RETIREMENT_TIMEOUT = 5.0
TEST_WORKER_ENV = "WG2_BEAT_TEST_WORKER"


def bounded_call(action: Callable[[], Any]) -> Any:
    """Bound retirement even if a public engine method fails to return."""
    done = threading.Event()
    result: list[Any] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            result.append(action())
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    if not done.wait(RETIREMENT_TIMEOUT):
        raise TimeoutError("BEAT engine retirement did not finish")
    if errors:
        raise errors[0]
    return result[0]


class _HostStream:
    def __init__(self, host: WorkerHost, events: Any, job: _Job) -> None:
        self.host, self.events, self.job = host, events, job

    def __next__(self) -> dict:
        try:
            return next(self.events)
        except StopIteration:
            raise
        except BaseException:
            # The engine may have failed while internally retiring its stream.
            # Its public API cannot distinguish that from other read failures.
            if not self.job.cancelled.is_set():
                self.host._fail_stop()
            raise

    def close(self) -> None:
        try:
            bounded_call(self.events.close)
        except BaseException:
            # Fail admission before ownership releases its slot on closure error.
            self.host._fail_stop()
            raise


class _HostSubmitter:
    def __init__(self, host: WorkerHost) -> None:
        self.host = host

    def submit(self, request_path: Any, **kwargs: Any) -> _HostStream:
        job = self.host._queue[0]  # The FIFO head remains held through stream retirement.
        try:
            return _HostStream(self.host, self.host._engine.submit(request_path, **kwargs), job)
        except BaseException:
            self.host._fail_stop()
            raise


class _Job:
    def __init__(self, message: dict, send: Callable[[dict], None], sequence: int) -> None:
        self.message, self._send, self.sequence = message, send, sequence
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.stream: OwnedStream | None = None
        self._output = threading.Lock()

    def send(self, message: dict) -> None:
        with self._output:
            if not self.done.is_set():
                self._send(message)

    def finish(self, reply: dict | None) -> None:
        with self._output:
            self.done.set()
            if reply is not None and not self.cancelled.is_set():
                with contextlib.suppress(OSError, ValueError):
                    self._send(reply)


def read_private_json(path: Path) -> dict[str, Any]:
    """Read bootstrap data with the same ownership checks as registry records."""
    with r._private_file(path) as fd:
        with os.fdopen(os.dup(fd), encoding="utf-8") as stream:
            value = json.load(stream)
    if not isinstance(value, dict):
        raise r.RecordRefused("Host bootstrap data must be an object")
    return value


def ready_path(identifier: str, directory: Path) -> Path:
    return r.record_path(identifier, directory).with_suffix(".ready.json")


def validate_key(key: dict[str, Any]) -> None:
    """Require a namespaced, fully resolved launch specification."""
    if not isinstance(key, dict):
        raise r.RecordRefused("Launch specification must be an object")
    if any(key.get(name) != value for name, value in r.host_key({}).items()):
        raise r.RecordRefused("Foreign provider or host protocol")
    if type(key.get("protocol_version")) is not int:
        raise r.RecordRefused("Invalid protocol version")
    if type(key.get("julia_threads")) is not int or key["julia_threads"] <= 0:
        raise r.RecordRefused("Host threads must already be resolved")
    for name in ("backend", "julia_executable", "julia_identity", "solver_script",
                 "engine_fingerprint", "runtime_fingerprint"):
        if not isinstance(key.get(name), str) or not key[name]:
            raise r.RecordRefused(f"Missing launch identity: {name}")
    for name in ("julia_project", "julia_sysimage"):
        if name not in key or (key[name] is not None and not isinstance(key[name], str)):
            raise r.RecordRefused(f"Invalid launch path: {name}")
    environment = key.get("environment")
    if not isinstance(environment, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()
    ):
        raise r.RecordRefused("Missing effective Julia environment")


class WorkerHost:
    """Publish before Julia startup; only authenticated clients extend lifetime."""

    def __init__(self, key: dict[str, Any], directory: Path, *, idle_timeout: float = DEFAULT_IDLE_TIMEOUT):
        validate_key(key)
        if not math.isfinite(idle_timeout) or idle_timeout <= 0:
            raise ValueError("Host idle timeout must be positive and finite")
        self.key, self.directory, self.idle_timeout = key, r.private_directory(directory), idle_timeout
        self.identifier = r.key_id(key)
        self.record: r.HostRecord | None = None
        self._server: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._engine: Any = None
        self._state = threading.Lock()
        self._connections: set[socket.socket] = set()
        self._clients = 0
        self._last_activity = time.monotonic()
        self._stopping = threading.Event()
        self._jobs = threading.Condition()
        self._queue: deque[_Job] = deque()
        self._sequence = 0
        self._worker_instance = r.new_token()
        self._ownership: StreamOwnership | None = None

    def _build_engine(self) -> Any:
        # Only tests set this hook; no BEAT import occurs in the application parent.
        injected = os.environ.get(TEST_WORKER_ENV)
        if injected:
            module, name = injected.split(":", 1)
            factory = getattr(importlib.import_module(module), name)
        else:
            from beat_engine import EngineWorker

            factory = EngineWorker
        return factory(
            julia_executable=self.key["julia_executable"],
            solver_script=Path(self.key["solver_script"]),
            julia_threads=self.key["julia_threads"],
            julia_project=Path(self.key["julia_project"]) if self.key["julia_project"] else None,
            julia_sysimage=Path(self.key["julia_sysimage"]) if self.key["julia_sysimage"] else None,
            environment=self.key["environment"],
        )

    def bind(self) -> r.HostRecord:
        """Bind while the caller holds spawn exclusion; publication is separate."""
        endpoint = endpoint_for(self.identifier, self.directory)
        self._server = endpoint.listen()
        if endpoint.kind == "unix":
            info = endpoint.path.lstat()
            self._socket_identity = info.st_dev, info.st_ino
        self.record = r.HostRecord(self.key, os.getpid(), r.new_token(), endpoint)
        r.validate_record(self.record, self.key, self.directory)
        self._engine = self._build_engine()
        self._ownership = StreamOwnership(_HostSubmitter(self))
        return self.record

    def _engine_report(self) -> dict[str, Any]:
        # The official ready announcement has no PID or per-Julia-process nonce.
        # This identifies the host-owned Python worker, not its private child.
        return {"host_pid": self.record.pid, "engine_pid": None,
                "worker_instance": self._worker_instance,
                "worker_info": self._engine.worker_info}

    def _fail_stop(self) -> None:
        self._stopping.set()
        with self._jobs:
            self._jobs.notify_all()

    def _run_job(self, job: _Job) -> None:
        reply: dict | None = None
        try:
            with self._jobs:
                self._jobs.wait_for(lambda: self._stopping.is_set() or job.cancelled.is_set()
                                    or self._queue[0] is job)
                if self._stopping.is_set() or job.cancelled.is_set():
                    return

            def status(message: str) -> None:
                if not job.done.is_set() and not job.cancelled.is_set():
                    try:
                        job.send({"type": "status", "message": str(message)})
                    except OSError:
                        job.cancelled.set()
                        raise

            if job.message["op"] == "ensure_started":
                self._engine.ensure_started(status_callback=status)
                reply = {"type": "ready", **self._engine_report()}
                return
            request = job.message["request"]
            stream = self._ownership.submit(Path(request) if isinstance(request, str) else request,
                                            operation=job.message.get("operation", "solve"),
                                            status_callback=status)
            job.stream = stream
            if job.cancelled.is_set():
                stream.close()
                return
            job.send({"type": "worker_info", **self._engine_report()})
            terminal = False
            for event in stream:
                if job.cancelled.is_set():
                    break
                if event.get("type") in {"completed", "cancelled", "failed"}:
                    reply = {"type": "event", "event": event}
                    terminal = True
                    break
                job.send({"type": "event", "event": event})
            if not terminal and not job.cancelled.is_set():
                raise RuntimeError("BEAT engine stream ended without a terminal event")
        except (OSError, ValueError, RuntimeError) as exc:
            # Transport failure can recover through stream closure. Other
            # engine errors are reported; failed closure itself stops admission.
            reply = {"type": "failed", "error": str(exc)}
        except BaseException as exc:
            self._fail_stop()
            reply = {"type": "failed", "error": str(exc)}
        finally:
            try:
                if job.stream is not None:
                    job.stream.close()
            except BaseException:
                self._fail_stop()
            finally:
                with self._jobs:
                    self._queue.remove(job)
                    self._jobs.notify_all()
                job.finish(reply)

    def _cancel_job(self, job: _Job) -> None:
        job.cancelled.set()
        with self._jobs:
            self._jobs.notify_all()
        try:
            if job.stream is not None:
                job.stream.cancel()  # Token-checked public stream retirement.
            if not job.done.wait(RETIREMENT_TIMEOUT):
                raise TimeoutError("BEAT submission did not retire")
        except BaseException:
            self._fail_stop()

    def _serve_job(self, connection: socket.socket, message: dict,
                   send: Callable[[dict], None]) -> None:
        with self._jobs:
            if self._stopping.is_set():
                raise RuntimeError("BEAT host admission is closed")
            self._sequence += 1
            job = _Job(message, send, self._sequence)
            self._queue.append(job)
        started = False
        try:
            send({"type": "queued", "sequence": job.sequence})
            threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
            started = True
            heartbeat = time.monotonic()
            while not job.done.is_set() and not self._stopping.is_set():
                if select.select([connection], [], [], 0.05)[0]:
                    # The client may already have received the terminal reply
                    # and sent its next control request while select waited.
                    if job.done.is_set():
                        return
                    control = receive_frame(connection, deadline=time.monotonic() + CONTROL_TIMEOUT)
                    if control is None or control.get("op") == "cancel":
                        self._cancel_job(job)
                        if control is not None:
                            send({"type": "cancelled"})
                        return
                    raise ValueError("Only cancellation is allowed during a submission")
                if time.monotonic() - heartbeat >= HEARTBEAT_INTERVAL:
                    job.send({"type": "heartbeat"})
                    heartbeat = time.monotonic()
        finally:
            if not started:
                with self._jobs:
                    self._queue.remove(job)
                    self._jobs.notify_all()
                job.done.set()
            elif not job.done.is_set():
                # Includes socket/write failures and partial cancellation frames.
                self._cancel_job(job)

    def serve(self) -> None:
        assert self._server is not None

        def stop(signum: int, frame: Any) -> None:
            self._stopping.set()

        for value in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(ValueError, OSError):
                signal.signal(value, stop)
        self._server.settimeout(min(0.1, self.idle_timeout))
        self._last_activity = time.monotonic()
        while not self._stopping.is_set():
            with self._state:
                if self._clients == 0 and time.monotonic() - self._last_activity >= self.idle_timeout:
                    self._stopping.set()
                    break
            try:
                connection, _ = self._server.accept()
            except socket.timeout:
                continue
            with self._state:
                if len(self._connections) >= 32:
                    connection.close()
                    continue
                self._connections.add(connection)
            threading.Thread(target=self._serve_connection, args=(connection,), daemon=True).start()

    def _serve_connection(self, connection: socket.socket) -> None:
        admitted = False
        deadline = time.monotonic() + CONTROL_TIMEOUT
        sending = threading.Lock()

        def send(message: dict) -> None:
            with sending:
                send_frame(connection, message)

        try:
            message = receive_frame(connection, deadline=deadline)
            if message is None or message.get("op") != "hello":
                raise r.RecordRefused("hello must come first")
            reply = r.auth_reply(self.record, message)
            challenge = r.new_token()
            send({**reply, "client_nonce": challenge, "idle_timeout_s": self.idle_timeout})
            while not self._stopping.is_set():
                if admitted:
                    if not select.select([connection], [], [], 0.1)[0]:
                        continue
                    deadline = time.monotonic() + CONTROL_TIMEOUT
                message = receive_frame(connection, deadline=deadline)
                if message is None:
                    return
                operation = message.get("op")
                if operation == "shutdown":
                    send(r.auth_reply(self.record, message))
                    self._fail_stop()
                    return
                if not admitted:
                    if (operation != "authenticate" or message.get("nonce") != challenge
                            or message.get("key") != self.key or message.get("key_id") != self.identifier
                            or any(message.get(k) != v for k, v in r.host_key({}).items())
                            or type(message.get("protocol_version")) is not int):
                        raise r.RecordRefused("Client authentication required")
                    proof = message.get("proof")
                    expected = r.auth_proof(self.record, challenge, "client_auth")
                    if not isinstance(proof, str) or not hmac.compare_digest(proof.encode(), expected.encode()):
                        raise r.RecordRefused("Client authentication proof mismatch")
                    with self._state:
                        if self._stopping.is_set():
                            return
                        self._clients += 1
                        admitted = True
                    connection.settimeout(CONTROL_TIMEOUT)
                    send({"type": "authenticated", "nonce": challenge,
                          "proof": r.auth_proof(self.record, challenge, "client_auth_ok")})
                    continue
                if operation == "ping":
                    send({"type": "pong", "host_pid": self.record.pid})
                elif operation == "adopt":
                    send({"type": "adopted", **self._engine_report()})
                elif operation in {"submit", "ensure_started"}:
                    request = message.get("request")
                    if operation == "submit" and (
                        not isinstance(request, (str, dict)) or not request
                        or (isinstance(request, str) and not Path(request).is_absolute())
                        or not isinstance(message.get("operation", "solve"), str)
                    ):
                        send({"type": "failed", "error": "Invalid BEAT submission request"})
                        continue
                    self._serve_job(connection, message, send)
                elif operation == "retire":
                    with self._jobs:
                        idle = not self._queue and not self._stopping.is_set()
                        if idle:
                            try:
                                bounded_call(self._engine.terminate)
                            except BaseException:
                                self._stopping.set()
                                self._jobs.notify_all()
                                raise RuntimeError("BEAT engine retirement failed") from None
                    send({"type": "retired", "engine_retired": idle})
                else:
                    send({"type": "failed", "error": f"Unknown host operation: {operation}"})
        except (OSError, ValueError, RuntimeError) as exc:
            with contextlib.suppress(OSError, ValueError):
                send({"type": "failed" if admitted else "hello_refused",
                      "reason": str(exc), "error": str(exc)})
        finally:
            with self._state:
                self._connections.discard(connection)
                if admitted:
                    self._clients -= 1
                    self._last_activity = time.monotonic()
            connection.close()

    def close(self) -> None:
        """Retire our engine and remove only our own record/socket under exclusion."""
        self._fail_stop()
        if self._server is not None:
            self._server.close()
        with self._state:
            for connection in self._connections:
                with contextlib.suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
        try:
            engine, self._engine = self._engine, None
            if self._ownership is not None:
                with contextlib.suppress(BaseException):
                    bounded_call(self._ownership.shutdown)
            if engine is not None:
                with contextlib.suppress(BaseException):
                    bounded_call(engine.terminate)
        finally:
            self._remove_own_record()

    def _remove_own_record(self) -> None:
        # cleanup_host may hold this lock while waiting for our exit. It then
        # removes the record itself; waiting here would deadlock that path.
        with contextlib.suppress(r.LockBusy):
            with r.SpawnLock(r.spawn_lock_path(self.identifier, self.directory), timeout=0):
                current = r.read_record(self.identifier, self.directory)
                if current is not None and current != self.record:
                    return
                if self.record is not None and self._socket_identity is not None:
                    path = self.record.endpoint.path
                    with contextlib.suppress(FileNotFoundError):
                        info = path.lstat()
                        if (info.st_dev, info.st_ino) == self._socket_identity:
                            path.unlink(missing_ok=True)
                if current is not None:
                    r.unlink_record(r.record_path(self.identifier, self.directory))
                ready = ready_path(self.identifier, self.directory)
                with contextlib.suppress(FileNotFoundError):
                    if self.record is not None and read_private_json(ready).get("record") == self.record.as_dict():
                        r.unlink_record(ready)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT)
    parser.add_argument("--ready", action="store_true", help="Parent publishes under its held spawn lock")
    parser.add_argument("--startup-timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.startup_timeout) or args.startup_timeout <= 0:
        raise ValueError("Host startup timeout must be positive and finite")
    directory = paths.checked_root(Path(args.dir)).absolute()
    key = read_private_json(Path(args.key))
    # The Windows native entry starts its interpreter from the bundle root.
    os.chdir(app_root())
    host = WorkerHost(key, directory, idle_timeout=args.idle_timeout)
    if Path(args.key).absolute() != r.launch_spec_path(host.identifier, directory):
        raise r.RecordRefused("Launch specification outside this host slot")
    try:
        if args.ready:
            # The parent keeps spawn exclusion until this exact child is published.
            # If it dies before publication, bootstrap times out and closes the host.
            record = host.bind()
            r._atomic_json(ready_path(host.identifier, directory), {
                "record": record.as_dict(), "launcher_pid": os.getppid(),
                "launcher_start": r.process_start_identity(os.getppid()),
            })
            deadline = time.monotonic() + args.startup_timeout
            while r.read_record(host.identifier, directory) != record:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Parent did not publish the host record")
                time.sleep(0.02)
        else:
            with r.SpawnLock(r.spawn_lock_path(host.identifier, directory)) as lock:
                if r.read_record(host.identifier, directory) is not None:
                    raise r.RecordRefused("Host slot already occupied")
                sweep_orphan_socket(host.identifier, directory, lock=lock)
                r.write_record(host.bind(), directory)
        host.serve()
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
