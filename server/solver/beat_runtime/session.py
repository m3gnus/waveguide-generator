"""Own one solve's staging, cancellation monitor and stream lifetime."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import contextlib
import json
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

from server.platform.temp_session import temporary_directory_root

from . import paths
from .manager import ManagedWorker, WorkerLease


class SessionCancelled(RuntimeError):
    """Cancellation ended a solve before it produced any results."""


class SolveSession:
    """Use as a context manager, including when no event is ever read."""

    def __init__(self, *, cancellation_callback: Callable[[], None] | None = None,
                 cancel_grace_s: float = 0.25) -> None:
        if not 0 <= cancel_grace_s < float("inf"):
            raise ValueError("Cancel grace must be finite and nonnegative")
        self.cancellation_callback = cancellation_callback
        self.cancel_grace_s = cancel_grace_s
        self.directory: Path | None = None
        self.cancel_path: Path | None = None
        self.request_path: Path | None = None
        self._temporary = None
        self._lease: WorkerLease | None = None
        self._stream = None
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._cancel_at = 0.0
        self._error: BaseException | None = None
        self._monitor: threading.Thread | None = None
        self._results = 0
        self._closed = False
        self._submitted = False
        self._lock = threading.RLock()

    def __enter__(self) -> SolveSession:
        root = temporary_directory_root()
        if root is not None:
            paths.checked_root(Path(root))
        self._temporary = tempfile.TemporaryDirectory(prefix="wg2-beat-solve-",
                                                      dir=temporary_directory_root(),
                                                      ignore_cleanup_errors=True)
        self.directory = Path(self._temporary.name)
        self.cancel_path = self.directory / "cancel.marker"
        self.request_path = self.directory / "request.json"
        return self

    def request_cancel(self) -> None:
        with self._lock:
            if self._closed or (self._lease is not None and self._lease.finished):
                return
            if not self._cancel.is_set():
                self._cancel_at = time.monotonic()
                self._cancel.set()
            if self.cancel_path is not None:
                with contextlib.suppress(OSError):
                    self.cancel_path.touch()

    def _check(self) -> None:
        if self._cancel.is_set():
            raise self._error or SessionCancelled("BEAT solve cancelled")
        if self.cancellation_callback is not None:
            self.cancellation_callback()

    def _watch(self) -> None:
        while not self._stop.wait(0.05):
            if not self._cancel.is_set() and self.cancellation_callback is not None:
                try:
                    self.cancellation_callback()
                except BaseException as exc:
                    self._error = exc
                    self.request_cancel()
            if self._cancel.is_set() and time.monotonic() - self._cancel_at >= self.cancel_grace_s:
                with self._lock:
                    lease = self._lease
                if lease is not None:
                    with contextlib.suppress(BaseException):
                        lease.cancel()
                return

    def submit(self, client: ManagedWorker, payload: Mapping[str, Any], *,
               negotiate: Callable[[dict | None, dict, str], Any] | None = None) -> None:
        if self.request_path is None or self._submitted or self._closed:
            raise RuntimeError("Session must be entered and submitted only once")
        self._submitted = True
        request = dict(payload, cancel_path=str(self.cancel_path.resolve()))
        # Serialize before admission: bad requests cannot disturb a warm worker.
        self.request_path.write_text(json.dumps(request, allow_nan=False), encoding="utf-8")
        self._check()
        self._monitor = threading.Thread(target=self._watch, name="beat-solve-cancel", daemon=True)
        self._monitor.start()
        try:
            lease = client.acquire(self._check)
            with self._lock:
                self._lease = lease
            self._check()
            lease.start()
            self._check()
            if negotiate is not None:
                negotiate(lease.client.worker_info, request, "solve")
            self._check()
            self._stream = lease.submit(self.request_path)
        except BaseException:
            with contextlib.suppress(BaseException):
                self.close()
            if self._error is not None:
                raise self._error
            if self._cancel.is_set():
                raise SessionCancelled("BEAT solve cancelled") from None
            raise

    def events(self) -> Iterator[dict]:
        if self._stream is None:
            raise RuntimeError("Session has no submission")
        try:
            while True:
                try:
                    event = next(self._stream)
                except BaseException as exc:
                    if self._cancel.is_set():
                        if not self._results:
                            raise self._error or SessionCancelled("BEAT solve cancelled")
                        yield {"type": "cancelled", "solved_count": self._results}
                        return
                    if isinstance(exc, StopIteration):
                        return
                    raise
                kind = event.get("type") if isinstance(event, dict) else None
                if kind == "result":
                    self._results += 1
                if kind in {"completed", "cancelled", "failed"}:
                    self._lease.finish(kind)
                    if kind == "cancelled" and not self._results and self._error is not None:
                        raise self._error
                    yield event
                    return
                yield event
        except BaseException:
            with contextlib.suppress(BaseException):
                self.close()
            raise
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
        error = None
        try:
            if self._monitor is not None:
                self._monitor.join(timeout=12.0)
                if self._monitor.is_alive():
                    raise RuntimeError("BEAT cancellation monitor did not stop")
        except BaseException as exc:
            error = exc
        try:
            if self._lease is not None:
                self._lease.close()
        except BaseException as exc:
            if error is None:
                error = exc
        finally:
            if self._temporary is not None:
                # A hosted cancel may still have surface.msh open on Windows.
                # TemporaryDirectory retries permission failures and defers
                # remaining removal to the application's swept session root.
                with contextlib.suppress(OSError):
                    self._temporary.cleanup()
        if error is not None:
            raise error

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None:
            self.close()
        else:
            with contextlib.suppress(BaseException):
                self.close()
