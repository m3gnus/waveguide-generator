"""Serialize host clients around EngineWorker's public closeable streams."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
import threading
from typing import Protocol


class EngineStream(Protocol):
    def __next__(self) -> dict: ...
    def close(self) -> None: ...


class EngineSubmitter(Protocol):
    def submit(self, request_path: Path | Mapping, *,
               status_callback: Callable[[str], None] | None = None,
               operation: str = "solve") -> EngineStream: ...


class OwnershipClosed(RuntimeError):
    """Admission ended, or a pending submission was cancelled."""


class OwnershipToken:
    """An opaque identity issued for one client's stream, never an engine token."""


class StreamOwnership:
    """Keep client ownership until public stream closure has finished.

    All clients of this worker must submit through this instance. Shutdown ends
    admission immediately; a submit still inside engine startup is closed when
    it returns. Startup timeout and engine lifetime remain engine/manager policy.
    """

    def __init__(self, worker: EngineSubmitter) -> None:
        self._worker = worker
        mutex = threading.Lock()
        self._changed = threading.Condition(mutex)
        self._parked = threading.Condition(mutex)
        self._holder: OwnershipToken | None = None
        self._events: EngineStream | None = None
        self._closed = False
        self._closing = False
        self._cancel_requested = False
        self._waiting = 0

    def _acquire(self) -> OwnershipToken:
        with self._changed:
            while self._holder is not None and not self._closed:
                self._waiting += 1
                self._parked.notify_all()
                try:
                    self._changed.wait()
                finally:
                    self._waiting -= 1
            if self._closed:
                raise OwnershipClosed("BEAT client stream admission is closed")
            self._holder = OwnershipToken()
            self._cancel_requested = False
            return self._holder

    def await_waiters(self, count: int = 1, timeout: float | None = None) -> bool:
        """Observe parked clients without polling or waking their waitset."""
        with self._parked:
            return self._parked.wait_for(lambda: self._waiting >= count, timeout)

    def holds(self, token: object) -> bool:
        with self._changed:
            return token is not None and self._holder is token and not self._closing

    def submit(self, request_path: Path | Mapping, *,
               status_callback: Callable[[str], None] | None = None,
               operation: str = "solve",
               event_callback: Callable[[dict], None] | None = None) -> OwnedStream:
        token = self._acquire()
        try:
            events = self._worker.submit(request_path, status_callback=status_callback, operation=operation)
        except BaseException:
            self._release(token)
            raise
        with self._changed:
            self._events = events
            cancelled = self._closed or self._cancel_requested
        stream = OwnedStream(self, token, events, event_callback)
        if cancelled:
            stream.close()
            raise OwnershipClosed("BEAT submission cancelled during startup")
        return stream

    def _release(self, token: OwnershipToken) -> None:
        with self._changed:
            if self._holder is token:
                self._holder = None
                self._events = None
                self._closing = False
                self._changed.notify_all()

    def cancel(self, token: object) -> bool:
        """Close only this owner's stream; stale or concurrent closes are no-ops."""
        return self._close(token)

    def _close(self, token: object, *, blocking: bool = True) -> bool:
        if not self._changed.acquire(blocking=blocking):
            return False
        try:
            if token is None or self._holder is not token or self._closing:
                return False
            if self._events is None:
                self._cancel_requested = True
                return True
            self._closing = True
            events = self._events
        finally:
            self._changed.release()
        try:
            # EngineWorker closes/retire its own submission safely, including
            # next() blocked on another thread. Never call worker.terminate().
            events.close()
        finally:
            self._release(token)
        return True

    def shutdown(self) -> None:
        """Wake every queued client before closing any active stream."""
        with self._changed:
            self._closed = True
            token = self._holder
            self._changed.notify_all()
        self.cancel(token)


class OwnedStream(Iterator[dict]):
    """A client token and stream; explicit close also works before the first read."""

    def __init__(self, owner: StreamOwnership, token: OwnershipToken, events: EngineStream,
                 callback: Callable[[dict], None] | None) -> None:
        self.token = token
        self._owner = owner
        self._events = events
        self._callback = callback
        self._closed = False

    def __iter__(self) -> OwnedStream:
        return self

    def __next__(self) -> dict:
        if self._closed or not self._owner.holds(self.token):
            raise StopIteration
        try:
            event = next(self._events)
            if not self._owner.holds(self.token):
                raise OwnershipClosed("BEAT submission cancelled during read")
            if event.get("type") in {"completed", "cancelled", "failed"}:
                self.close()
            if self._callback is not None:
                self._callback(event)
            return event
        except BaseException:
            try:
                self.close()
            except Exception:
                pass  # Keep the read/callback error; closure still released ownership.
            raise

    def close(self) -> None:
        self._closed = True
        self._owner.cancel(self.token)

    def cancel(self) -> bool:
        self._closed = True
        return self._owner.cancel(self.token)

    def __del__(self) -> None:
        try:
            self._owner._close(self.token, blocking=False)
        except BaseException:
            pass
