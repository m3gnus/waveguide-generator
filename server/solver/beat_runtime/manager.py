"""Resolve and retain official BEAT clients for solves and warm-up."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
import os
from pathlib import Path
import threading
from typing import Any

from . import assets, discovery, identity, paths, registry, threads
from .client import HostedWorker
from .host import bounded_call, official_engine_factory
from .ownership import OwnershipClosed, StreamOwnership


def resolve_key(backend: str, *, julia_executable: str | None = None,
                julia_threads: int | str = "auto", julia_project: Path | None = None,
                julia_sysimage: Path | None = None, solver_script: Path | None = None,
                environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Snapshot content, paths, threads and the exact effective launch environment."""
    if backend not in {"cpu", "metal"}:
        raise ValueError("BEAT runtime supports CPU and Metal")
    env = dict(os.environ if environment is None else environment)
    executable = discovery.discover_julia(julia_executable, environ=env)
    if executable is None:
        raise discovery.JuliaDiscoveryError("Julia executable is unavailable")
    engine = assets.engine_assets(backend)
    project = (engine.project if julia_project is None else julia_project).expanduser().resolve()
    solver = (engine.system_solver if solver_script is None else solver_script).expanduser().resolve()
    sysimage = julia_sysimage.expanduser().resolve() if julia_sysimage is not None else None
    count = threads.resolve_julia_threads(backend, julia_threads)
    env["JULIA_NUM_THREADS"] = str(count)
    env.setdefault("JULIA_DEPOT_PATH", str(paths.runtime_dir(environ=env) / "depot"))
    return registry.host_key({
        "backend": backend, "julia_executable": str(Path(executable).resolve()),
        "julia_identity": discovery.executable_identity(Path(executable)),
        "solver_script": str(solver), "solver_identity": discovery.executable_identity(solver),
        "julia_project": str(project),
        "julia_sysimage": str(sysimage) if sysimage else None, "julia_threads": count,
        "engine_fingerprint": identity.engine_fingerprint(
            engine, julia_project=project, julia_sysimage=sysimage),
        "runtime_fingerprint": identity.runtime_fingerprint(
            compiled_request_policy=Path(__file__).resolve().parents[1] / "official_beat.py"),
        "environment": env,
    })


class WorkerLease:
    """Hold admission across startup, negotiation, submission and retirement."""

    def __init__(self, client: ManagedWorker) -> None:
        self.client = client
        self.stream = None
        self.finished = False
        self.cancelled = False
        self.busy = False
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self.cancelled:
                raise OwnershipClosed("BEAT session cancelled before startup")
            self.busy = True
        try:
            self.client.worker.ensure_started()
        finally:
            with self._lock:
                self.busy = False
        if self.cancelled:
            # Cancellation can win just before ensure_started enters the engine.
            # Retire again after it returns, while this lease still excludes reuse.
            if self.client.mode == "child":
                try:
                    bounded_call(self.client.worker.terminate)
                except BaseException:
                    self.client.unusable = True
                    raise
            raise OwnershipClosed("BEAT session cancelled during startup")

    def submit(self, request: Path, **kwargs: Any) -> Any:
        with self._lock:
            if self.cancelled:
                raise OwnershipClosed("BEAT session cancelled before submission")
            self.busy = True
        try:
            stream = self.client.ownership.submit(request, **kwargs)
            with self._lock:
                self.stream = stream
                if self.cancelled:
                    stream.close()
                    raise OwnershipClosed("BEAT session cancelled during submission")
            return stream
        finally:
            with self._lock:
                self.busy = False

    def cancel(self) -> None:
        with self._lock:
            # The lease stays held even when OwnedStream releases on a terminal.
            # No backstop can retire a successor, including a stale session close.
            if not self.client.holds(self) or self.finished or self.cancelled:
                return
            self.cancelled = True
            if self.client.mode == "child" and (self.busy or self.stream is not None):
                error = None
                actions = [self.client.worker.terminate]
                if self.stream is not None:
                    actions.append(self.stream.close)
                for action in actions:
                    try:
                        bounded_call(action)
                    except BaseException as exc:
                        if error is None:
                            error = exc
                if error is not None:
                    self.client.unusable = True
                    raise error
            elif self.stream is not None:
                try:
                    bounded_call(self.stream.close)
                except BaseException:
                    self.client.unusable = True
                    raise
            elif self.busy:
                # Disconnect only this hosted client. Host retires its owned job;
                # another client's work must never receive a global shutdown.
                self.client.unusable = True
                self.client.worker.detach()

    def finish(self, kind: str) -> None:
        with self._lock:
            self.finished = True
            if kind == "failed" and self.client.mode == "child":
                # This outer lease still excludes every subsequent session.
                try:
                    bounded_call(self.client.worker.terminate)
                except BaseException:
                    self.client.unusable = True
                    raise

    def close(self) -> None:
        try:
            if not self.finished and (self.busy or self.stream is not None):
                self.cancel()
            if self.stream is not None:
                bounded_call(self.stream.close)
        except BaseException:
            self.client.unusable = True
            raise
        finally:
            self.client.release(self)


class ManagedWorker:
    """A cached worker and a cancellable local admission gate."""

    def __init__(self, worker: Any, mode: str) -> None:
        self.worker, self.mode = worker, mode
        self.ownership = StreamOwnership(worker)
        self.unusable = False
        self._changed = threading.Condition()
        self._lease: WorkerLease | None = None
        self._closed = False

    @property
    def worker_info(self) -> dict | None:
        return self.worker.worker_info

    def acquire(self, check: Callable[[], None]) -> WorkerLease:
        with self._changed:
            while self._lease is not None and not self._closed and not self.unusable:
                check()
                self._changed.wait(0.05)
            check()
            if self._closed or self.unusable:
                raise OwnershipClosed("BEAT manager admission is closed")
            self._lease = WorkerLease(self)
            return self._lease

    def holds(self, lease: WorkerLease) -> bool:
        with self._changed:
            return self._lease is lease

    def release(self, lease: WorkerLease) -> None:
        with self._changed:
            if self._lease is lease:
                self._lease = None
                self._changed.notify_all()

    def close_admission(self) -> None:
        with self._changed:
            self._closed = True
            self._changed.notify_all()

    def close(self, *, detach: bool) -> None:
        self.close_admission()
        release = (lambda: bounded_call(self.worker.terminate)) if self.mode == "child" else (
            self.worker.detach if detach else self.worker.shutdown)
        error = None
        for action in (self._lease.cancel if self._lease else lambda: None,
                       self.ownership.shutdown, release):
            try:
                action()
            except BaseException as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error


class WorkerManager:
    """Host mode by default; child fallback is always an explicit caller choice."""

    def __init__(self, *, mode: str = "host", directory: Path | None = None,
                 engine_factory: Callable[..., Any] | None = None) -> None:
        if mode not in {"host", "child"}:
            raise ValueError("BEAT worker mode must be host or child")
        if engine_factory is not None and mode != "child":
            raise ValueError("An engine factory requires child mode")
        self.mode, self.directory = mode, directory
        self._factory = engine_factory or official_engine_factory
        self._lock = threading.Lock()
        self._closed = False
        self._clients: dict[str, ManagedWorker] = {}

    def get_worker(self, backend: str = "cpu", **options: Any) -> ManagedWorker:
        with self._lock:
            if self._closed:
                raise OwnershipClosed("BEAT manager admission is closed")
            key = resolve_key(backend, **options)
            identifier = registry.key_id(key)
            client = self._clients.get(identifier)
            if client is None or client.unusable:
                if client is not None:
                    client.close(detach=True)
                if self.mode == "host":
                    worker = HostedWorker(key, directory=self.directory)
                else:
                    worker = self._factory(
                        julia_executable=key["julia_executable"], solver_script=Path(key["solver_script"]),
                        julia_project=Path(key["julia_project"]),
                        julia_sysimage=Path(key["julia_sysimage"]) if key["julia_sysimage"] else None,
                        julia_threads=key["julia_threads"], environment=copy.deepcopy(key["environment"]),
                        backend_label=f"BEAT {backend}",
                    )
                client = self._clients[identifier] = ManagedWorker(worker, self.mode)
            return client

    def _close(self, *, detach: bool) -> None:
        with self._lock:
            self._closed = True
            clients, self._clients = list(self._clients.values()), {}
            for client in clients:
                client.close_admission()
        error = None
        for client in clients:
            try:
                client.close(detach=detach)
            except BaseException as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error

    def shutdown(self) -> None:
        """Close admission and release every client, even after a teardown error."""
        self._close(detach=False)

    def detach(self) -> None:
        """Quit: retain idle hosts; cancel active work and terminate child mode."""
        self._close(detach=True)


_default_manager = WorkerManager()


def get_manager() -> WorkerManager:
    return _default_manager
