"""Detached owner of one optional official BEAT EngineWorker."""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable
import contextlib
import hmac
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
from .clock import suspend_aware_monotonic
from .ipc import endpoint_for, receive_frame, send_frame
from .ownership import OwnedStream, StreamOwnership

DEFAULT_IDLE_TIMEOUT = 1800.0
CONTROL_TIMEOUT = 2.0
HEARTBEAT_INTERVAL = 0.5
RETIREMENT_TIMEOUT = 5.0
PREAUTH_TIMEOUT = 0.5
AUTH_TIMEOUT = 1.0
STREAM_SEND_TIMEOUT = 10.0
MAX_CLIENTS = 32
MAX_PENDING = 8
ACCEPT_ERROR_BUDGET = 5


def official_engine_factory(**kwargs: Any) -> Any:
    """Import the optional official worker only inside the host."""
    from beat_engine import EngineWorker

    return EngineWorker(**kwargs)


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
        except BaseException as exc:
            self.host._log(f"stream retirement failed: {exc}")
            if self.job.client_cancelled:
                # Keep the FIFO slot through normal engine retirement. A close
                # error during explicit cancellation need not stop the host.
                try:
                    bounded_call(self.host._engine.terminate)
                    return
                except BaseException as retirement:
                    self.host._log(f"engine retirement failed: {retirement}")
            # Fail admission before ownership releases its slot on closure error.
            self.host._fail_stop()
            raise


class _HostSubmitter:
    def __init__(self, host: WorkerHost) -> None:
        self.host = host

    def submit(self, request_path: Any, **kwargs: Any) -> _HostStream:
        job = self.host._queue[0]  # The FIFO head remains held through stream retirement.
        self.host._ensure_engine(status_callback=kwargs.get("status_callback"))
        try:
            return _HostStream(self.host, self.host._engine.submit(request_path, **kwargs), job)
        except (FileNotFoundError, PermissionError) as exc:
            # Public submit can fail in Popen before it opens the request.
            # Identify executable errors by filename or current executable state.
            executable = Path(self.host.key["julia_executable"])
            if (getattr(exc, "filename", None) == str(executable)
                    or (not isinstance(request_path, Path)
                        and (not executable.is_file() or not os.access(executable, os.X_OK)))):
                self.host._fail_stop()
                raise RuntimeError(f"BEAT Julia executable failed ({executable}): {exc}") from exc
            raise ValueError(f"Invalid BEAT request file: {exc}") from exc
        except (IsADirectoryError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid BEAT request file: {exc}") from exc
        except BaseException:
            self.host._fail_stop()
            raise


class _Job:
    def __init__(self, message: dict, send: Callable[[dict], None], sequence: int) -> None:
        self.message, self._send, self.sequence = message, send, sequence
        self.cancelled = threading.Event()
        self.client_cancelled = False
        self.done = threading.Event()
        self.stream: OwnedStream | None = None
        self.running = False
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


def validate_key(key: dict[str, Any]) -> dict[str, Any]:
    """Require a namespaced, fully resolved launch specification."""
    if not isinstance(key, dict):
        raise r.RecordRefused("Launch specification must be an object")
    key = dict(key)
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
        if key[name] == "":
            key[name] = None
    for name in ("solver_script", "julia_executable", "julia_project", "julia_sysimage"):
        if key[name] is not None and not Path(key[name]).is_absolute():
            raise r.RecordRefused(f"Launch path must be absolute: {name}")
    environment = key.get("environment")
    if not isinstance(environment, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()
    ):
        raise r.RecordRefused("Missing effective Julia environment")
    return key


def validate_request_file(request: str) -> None:
    """Reject ordinary input failures before queueing or starting the engine."""
    try:
        value = json.loads(Path(request).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request must be a JSON object")
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid BEAT request file {request}: {exc}") from exc


class WorkerHost:
    """Publish before Julia startup; admitted connections keep the host alive.

    Managers retain an admitted connection between submissions, until detach.
    """

    def __init__(
        self, key: dict[str, Any], directory: Path, *, idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        engine_factory: Callable[..., Any] | None = None,
    ) -> None:
        key = validate_key(key)
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
        self._pending: dict[socket.socket, float] = {}
        self._engine_factory = engine_factory if engine_factory is not None else official_engine_factory
        self._logging = threading.Lock()
        self._clients = 0
        # Idle age must include suspend. Keep control/IPC, heartbeat and
        # retirement deadlines on time.monotonic() so they retain their existing
        # behavior on resume (in particular, sleep exclusion on macOS/Linux).
        self._last_activity = suspend_aware_monotonic()
        self._stopping = threading.Event()
        self._jobs = threading.Condition()
        self._queue: deque[_Job] = deque()
        self._sequence = 0
        self._worker_instance = r.new_token()
        self._ownership: StreamOwnership | None = None

    def _build_engine(self) -> Any:
        return self._engine_factory(
            julia_executable=self.key["julia_executable"],
            solver_script=Path(self.key["solver_script"]),
            julia_threads=self.key["julia_threads"],
            julia_project=Path(self.key["julia_project"]) if self.key["julia_project"] else None,
            julia_sysimage=Path(self.key["julia_sysimage"]) if self.key["julia_sysimage"] else None,
            environment={**os.environ, **self.key["environment"]},
        )

    def _log(self, message: str) -> None:
        with self._logging, contextlib.suppress(OSError, r.RecordRefused):
            with r._private_file(r.log_path(self.identifier, self.directory), create=True, append=True) as fd:
                os.write(fd, f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n".encode())

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

    def _ensure_engine(self, **kwargs: Any) -> None:
        try:
            self._engine.ensure_started(**kwargs)
        except (FileNotFoundError, PermissionError) as exc:
            message = f"BEAT Julia executable failed ({self.key['julia_executable']}): {exc}"
            self._log(message)
            try:
                self._queue[0].send({"type": "failed", "error": message})
            finally:
                self._fail_stop()
            raise RuntimeError(message) from exc
        except BaseException:
            if not self._queue[0].cancelled.is_set():
                self._fail_stop()
            raise

    def _run_job(self, job: _Job) -> None:
        reply: dict | None = None
        try:
            with self._jobs:
                self._jobs.wait_for(lambda: self._stopping.is_set() or job.cancelled.is_set()
                                    or self._queue[0] is job)
                if self._stopping.is_set() or job.cancelled.is_set():
                    return
                job.running = True

            def status(message: str) -> None:
                if not job.done.is_set() and not job.cancelled.is_set():
                    try:
                        job.send({"type": "status", "message": str(message)})
                    except OSError:
                        job.cancelled.set()

            if job.message["op"] == "ensure_started":
                self._ensure_engine(status_callback=status)
                if job.cancelled.is_set():
                    bounded_call(self._engine.terminate)
                    return
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
                    if event.get("type") == "failed":
                        # Keep the FIFO slot through retirement, including for
                        # callers that use HostedWorker without a WG manager.
                        try:
                            bounded_call(self._engine.terminate)
                        except BaseException:
                            self._fail_stop()
                            raise
                    reply = {"type": "event", "event": event}
                    terminal = True
                    break
                job.send({"type": "event", "event": event})
            if not terminal and not job.cancelled.is_set():
                raise RuntimeError("BEAT engine stream ended without a terminal event")
        except (OSError, ValueError, RuntimeError) as exc:
            # Transport failure can recover through stream closure. Other
            # engine errors are reported; failed closure itself stops admission.
            self._log(f"submission failed: {exc}")
            reply = {"type": "failed", "error": str(exc)}
        except BaseException as exc:
            if not job.cancelled.is_set():
                self._fail_stop()
            self._log(f"submission failed: {exc}")
            reply = {"type": "failed", "error": str(exc)}
        finally:
            try:
                if job.stream is not None:
                    job.stream.close()
            except BaseException as exc:
                self._log(f"submission retirement failed: {exc}")
                self._fail_stop()
            finally:
                with self._jobs:
                    self._queue.remove(job)
                    self._jobs.notify_all()
                job.finish(reply)

    def _cancel_job(self, job: _Job, *, client_cancelled: bool = False,
                    retire_startup: bool = False) -> None:
        job.client_cancelled |= client_cancelled
        job.cancelled.set()
        with self._jobs:
            self._jobs.notify_all()
        if job.stream is None and retire_startup and job.running:
            deadline = time.monotonic() + RETIREMENT_TIMEOUT
            try:
                bounded_call(self._engine.terminate)
                if not job.done.wait(max(0, deadline - time.monotonic())):
                    raise TimeoutError("BEAT cancelled startup did not retire")
            except BaseException as exc:
                self._log(f"startup retirement failed: {exc}")
                self._fail_stop()
            return
        if job.stream is None:
            # Cold startup has no stream to retire yet. The runner retains its
            # FIFO slot and closes the stream immediately when submit returns.
            return
        try:
            if job.stream is not None:
                job.stream.cancel()  # Token-checked public stream retirement.
            if not job.done.wait(RETIREMENT_TIMEOUT):
                raise TimeoutError("BEAT submission did not retire")
        except BaseException as exc:
            self._log(f"cancellation retirement failed: {exc}")
            self._fail_stop()

    def _serve_job(self, connection: socket.socket, message: dict,
                   send: Callable[[dict], None]) -> None:
        connection.settimeout(STREAM_SEND_TIMEOUT)
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
                        self._cancel_job(job, client_cancelled=control is not None,
                                         retire_startup=bool(control and control.get("retire_startup")))
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
        self._last_activity = suspend_aware_monotonic()
        self._log(f"serving {self.identifier} (idle {self.idle_timeout:g}s)")
        accept_errors = 0
        retrying_accept = False
        while not self._stopping.is_set():
            with self._state:
                # Finish an active, bounded accept-error sequence even if log
                # writes or scheduling take it past the ordinary idle deadline.
                # A peer mid-handshake keeps the host: after a long sleep the idle
                # clock can be far past its deadline while the handshake is not.
                if (not retrying_accept and self._clients == 0 and not self._pending
                        and suspend_aware_monotonic() - self._last_activity >= self.idle_timeout):
                    self._log("idle exit")
                    self._stopping.set()
                    break
            try:
                connection, _ = self._server.accept()
            except socket.timeout:
                # Restore idle-exit eligibility without forgiving prior errors;
                # only an accepted connection resets the original error budget.
                retrying_accept = False
                continue
            except OSError as exc:
                accept_errors += 1
                retrying_accept = True
                self._log(f"accept failed ({accept_errors}/{ACCEPT_ERROR_BUDGET}): {exc}")
                if accept_errors >= ACCEPT_ERROR_BUDGET:
                    self._fail_stop()
                    break
                self._stopping.wait(0.02)
                continue
            accept_errors = 0
            retrying_accept = False
            with self._state:
                self._last_activity = suspend_aware_monotonic()
                # Pending peers have their own small budget. Evict the oldest
                # so silent peers cannot reserve every authenticated client slot.
                if len(self._pending) >= MAX_PENDING:
                    oldest = next(iter(self._pending))
                    self._pending.pop(oldest)
                    with contextlib.suppress(OSError):
                        oldest.shutdown(socket.SHUT_RDWR)
                    oldest.close()
                self._pending[connection] = time.monotonic() + PREAUTH_TIMEOUT
                self._connections.add(connection)
            threading.Thread(target=self._serve_connection, args=(connection,), daemon=True).start()

    def _serve_connection(self, connection: socket.socket) -> None:
        admitted = False
        with self._state:
            deadline = self._pending.get(connection, time.monotonic() + PREAUTH_TIMEOUT)
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
            deadline = time.monotonic() + AUTH_TIMEOUT
            while not self._stopping.is_set():
                if admitted:
                    if not select.select([connection], [], [], 0.1)[0]:
                        continue
                    deadline = time.monotonic() + CONTROL_TIMEOUT
                    connection.settimeout(CONTROL_TIMEOUT)
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
                        if self._clients >= MAX_CLIENTS:
                            raise r.RecordRefused("Authenticated client capacity reached")
                        self._pending.pop(connection, None)
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
                    if operation == "submit" and isinstance(request, str):
                        try:
                            validate_request_file(request)
                        except ValueError as exc:
                            send({"type": "failed", "error": str(exc)})
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
            self._log(f"connection failed: {exc}")
            with contextlib.suppress(OSError, ValueError):
                send({"type": "failed" if admitted else "hello_refused",
                      "reason": str(exc), "error": str(exc)})
        finally:
            with self._state:
                self._pending.pop(connection, None)
                self._connections.discard(connection)
                if admitted:
                    self._clients -= 1
                    self._last_activity = suspend_aware_monotonic()
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
        # Unpublish before slow retirement. A spawner holding the lock can
        # instead prune after our exit through the locked cleanup policy.
        try:
            self._remove_own_record()
        except (OSError, r.RecordRefused) as exc:
            self._log(f"record removal failed: {exc}")
        try:
            engine, self._engine = self._engine, None
            if self._ownership is not None:
                try:
                    bounded_call(self._ownership.shutdown)
                except BaseException as exc:
                    self._log(f"ownership retirement failed: {exc}")
            if engine is not None:
                try:
                    bounded_call(engine.terminate)
                except BaseException as exc:
                    self._log(f"engine retirement failed: {exc}")
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
    host = WorkerHost(key, directory, idle_timeout=args.idle_timeout, engine_factory=official_engine_factory)
    if Path(args.key).absolute() != r.launch_spec_path(host.identifier, directory):
        raise r.RecordRefused("Launch specification outside this host slot")
    with r._private_file(r.log_path(host.identifier, directory), create=True) as fd:
        os.ftruncate(fd, 0)
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
    except BaseException as exc:
        host._log(f"host failed: {exc}")
        raise
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
