"""Serialize host clients around EngineWorker's public closeable streams."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
import queue
import threading
from typing import Protocol

_retirements: queue.SimpleQueue[tuple[StreamOwnership, OwnershipToken]] = queue.SimpleQueue()
_reaper_lock = threading.Lock()
_reaper_started = False


def _reap() -> None:
    while True:
        owner, token = _retirements.get()
        try:
            owner.cancel(token)
        except BaseException:
            pass  # Closure releases ownership even when engine retirement fails.
        finally:
            del owner, token


def _ensure_reaper() -> None:
    global _reaper_started
    with _reaper_lock:
        if not _reaper_started:
            threading.Thread(target=_reap, name="beat-stream-reaper", daemon=True).start()
            _reaper_started = True


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

    def __init__(self) -> None:
        self.callback_error: BaseException | None = None
        self._callback_lock = threading.Lock()


class StreamOwnership:
    """Keep client ownership until public stream closure has finished.

    All clients of this worker must submit through this instance. Shutdown ends
    admission immediately; a submit still inside engine startup is closed when
    it returns. Startup timeout and engine lifetime remain engine/manager policy.
    Waiters have no FIFO guarantee. Concurrent closes wait up to
    retirement_timeout_s for retirement, then raise TimeoutError.
    """

    def __init__(self, worker: EngineSubmitter, *, retirement_timeout_s: float = 5.0) -> None:
        if retirement_timeout_s <= 0:
            raise ValueError("retirement_timeout_s must be positive")
        self._worker = worker
        self._retirement_timeout_s = retirement_timeout_s
        mutex = threading.Lock()
        self._changed = threading.Condition(mutex)
        self._parked = threading.Condition(mutex)
        self._holder: OwnershipToken | None = None
        self._events: EngineStream | None = None
        self._closed = False
        self._closing = False
        self._closing_thread: int | None = None
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
        _ensure_reaper()  # Never start a thread or take a mutex in a finalizer.
        token = self._acquire()

        def guarded_status(message: str) -> None:
            # EngineWorker also invokes this under its mutex and on its stderr
            # reader. Record once; only the reaper may retire from this path.
            with token._callback_lock:
                if token.callback_error is not None:
                    return
            try:
                if status_callback is not None:
                    status_callback(message)
            except BaseException as exc:
                with token._callback_lock:
                    if token.callback_error is None:
                        token.callback_error = exc
                        _retirements.put((self, token))

        try:
            events = self._worker.submit(request_path, status_callback=guarded_status, operation=operation)
        except BaseException:
            self._release(token)
            raise
        with self._changed:
            self._events = events
            cancelled = self._closed or self._cancel_requested or token.callback_error is not None
        stream = OwnedStream(self, token, events, event_callback)
        if cancelled:
            try:
                stream.close()
            finally:
                if token.callback_error is not None:
                    raise token.callback_error
            raise OwnershipClosed("BEAT submission cancelled during startup")
        return stream

    def _release(self, token: OwnershipToken) -> None:
        with self._changed:
            if self._holder is token:
                self._holder = None
                self._events = None
                self._closing = False
                self._closing_thread = None
                self._changed.notify_all()

    def cancel(self, token: object) -> bool:
        """Close this owner's stream; wait for a concurrent retirement to finish."""
        return self._close(token)

    def _close(self, token: object) -> bool:
        with self._changed:
            if token is None or self._holder is not token:
                return False
            if self._closing:
                if self._closing_thread == threading.get_ident():
                    return False
                if not self._changed.wait_for(lambda: self._holder is not token,
                                             self._retirement_timeout_s):
                    raise TimeoutError("BEAT stream retirement is still in progress")
                return False
            if self._events is None:
                self._cancel_requested = True
                return True
            self._closing = True
            self._closing_thread = threading.get_ident()
            events = self._events
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

    @property
    def callback_error(self) -> BaseException | None:
        """The first status callback failure, including asynchronous failures."""
        return self.token.callback_error

    def __next__(self) -> dict:
        if self.callback_error is not None:
            self.close()
            raise self.callback_error
        if self._closed or not self._owner.holds(self.token):
            raise StopIteration
        try:
            event = next(self._events)
            terminal = event.get("type") in {"completed", "cancelled", "failed"}
            if not terminal and not self._owner.holds(self.token):
                raise OwnershipClosed("BEAT submission cancelled during read")
            if terminal:
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
            # SimpleQueue.put is reentrant and never blocks: cyclic GC may
            # interrupt a caller holding either WG's or the engine's mutex.
            _retirements.put((self._owner, self.token))
        except BaseException:
            pass
