"""Activity and installer retirement for ensure-started backends."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import os
import threading
import time

from server.app import QUIET_REQUEST_ROUTES
from server.platform.instance import StopSignal
from server.solver.beat_runtime import retire
from server.updates.restart import RetirementHold

log = logging.getLogger("wg.launch")
RETIRE_REASON = "the installer is replacing it"


class ActivityTracker:
    """Count clients on the serving loop, including failed and disconnected scopes."""

    def __init__(self, app, *, clock: Callable[[], float] = time.monotonic, timeout: float = 2.0):
        self.app = app
        self.clock = clock
        self.timeout = timeout
        self.loop: asyncio.AbstractEventLoop | None = None
        self.http_requests = 0
        self.websockets = 0
        self.last_activity = clock()

    async def __call__(self, scope, receive, send):
        self.loop = asyncio.get_running_loop()
        websocket = scope["type"] == "websocket"
        http = scope["type"] == "http" and scope["path"] != "/health" and (
            scope["method"], scope["path"]
        ) not in QUIET_REQUEST_ROUTES
        if http:
            self.http_requests += 1
        if websocket:
            self.websockets += 1
        if http or websocket:
            self.last_activity = self.clock()
        try:
            await self.app(scope, receive, send)
        finally:
            if http:
                self.http_requests -= 1
            if websocket:
                self.websockets -= 1
            if http or websocket:
                self.last_activity = self.clock()

    async def holds_work(self) -> bool:
        delivery_in_flight = getattr(self.app.state, "cad_delivery_in_flight", None)
        if delivery_in_flight is not None:
            try:
                if delivery_in_flight():
                    return True
            except Exception:  # noqa: BLE001 - uncertain delivery activity owns work
                return True
        runtime = getattr(self.app.state, "jobs_runtime", None)
        if runtime is not None:
            if runtime.running_job_ids or getattr(runtime, "_prep_running", ()) or getattr(runtime, "_prep_queue", ()):
                return True
            # Count every preparing row; lane claims and manual waits still own work.
            _rows, total = await asyncio.to_thread(
                runtime.store.list_jobs, statuses=("queued", "running", "preparing"), limit=1
            )
            if total:
                return True
        return bool(self.http_requests)

    async def _has_client(self) -> bool:
        return await self.holds_work() or bool(self.websockets)

    def _on_loop(self, check: Callable, *, on_error=True):
        if self.loop is None or not self.loop.is_running():
            return on_error
        future = None
        coroutine = None
        try:
            coroutine = check()
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
            return future.result(timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - retirement must fail closed on any doubt
            if (
                isinstance(exc, TimeoutError) and future is not None and future.done()
                and not future.cancelled() and future.exception() is None
            ):
                return future.result()
            if future is not None:
                future.cancel()
            elif coroutine is not None:
                coroutine.close()
            log.exception("Could not check backend activity; keeping it running")
            return on_error

    def has_client(self) -> bool:
        return self._on_loop(self._has_client)

    def retire_if_idle(
        self, request_stop: Callable[[str], None], reason: str,
        *, requested: Callable[[], bool] = lambda: True,
    ) -> bool:
        async def commit() -> bool:
            if getattr(self.app.state, "retiring", False):
                return True
            if await self._has_client() or not requested():
                return False
            self.app.state.retiring = True
            log.info("Retiring idle headless backend (%s)", reason)
            try:
                request_stop(reason)
            except BaseException:
                self.app.state.retiring = False
                raise
            return True

        return self._on_loop(commit, on_error=False)


class RetirementWatcher:
    """A clock-driven hold that can re-arm only after the installer event clears."""

    def __init__(
        self, hold: RetirementHold, has_client: Callable[[], bool], commit: Callable[[], bool],
        *, clock: Callable[[], float] = time.monotonic, cap: float = 130.0,
        poll_interval: float = 1.0, reason: str = RETIRE_REASON, armed: bool = True,
    ):
        self.hold = hold
        self.has_client = has_client
        self.commit = commit
        self.clock = clock
        self.cap = cap
        self.poll_interval = poll_interval
        self.reason = reason
        self.armed = armed
        self.held_since: float | None = None
        self.retired = False

    def step(self, requested: bool) -> None:
        if self.retired:
            return
        if not requested:
            if self.held_since is not None:
                self._release("installer event cleared")
            self.armed = True
            return
        if not self.armed:
            return
        if self.held_since is None:
            self.hold.hold(self.reason)
            self.held_since = self.clock()
            log.info("Holding new work for backend retirement (%s)", self.reason)
        if self.clock() - self.held_since >= self.cap:
            self._release("installer retirement wait expired")
            return
        try:
            if not self.has_client():
                self.retired = self.commit()
        except Exception:  # noqa: BLE001 - an uncertain predicate must never retire
            log.exception("Could not check backend clients; keeping retirement hold")

    def _release(self, reason: str) -> None:
        self.hold.release()
        self.held_since = None
        self.armed = False
        log.info("Backend retirement cancelled (%s)", reason)


class InstallerRetireThread:
    """Own the named event and park in a kernel wait while it is clear."""

    def __init__(
        self, tracker: ActivityTracker, request_stop: Callable[[str], None], signal: retire.RetireSignal,
        *, initially_requested: bool,
    ):
        self.tracker = tracker
        self.request_stop = request_stop
        self.stop = StopSignal()
        self.thread: threading.Thread | None = None
        self.signal = signal
        self.initially_requested = initially_requested

    def start(self) -> None:
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        event_name = retire.RETIRE_IDLE_EVENT
        if self.signal.error is not None:
            log.warning("Could not create backend retire event (WinError %s)", self.signal.error)
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.WaitForMultipleObjects.argtypes = (
            wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD,
        )
        kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        event = kernel32.OpenEventW(0x00100000, False, event_name)  # SYNCHRONIZE
        if not event or not self.stop.win32_handle:
            log.warning("Could not open waitable backend retire events (WinError %s)", ctypes.get_last_error())
            if event:
                kernel32.CloseHandle(event)
            self.stop.close()
            return
        watcher = RetirementWatcher(
            self.tracker.app.state.retirement_hold, self.tracker.has_client,
            lambda: self.tracker.retire_if_idle(
                self.request_stop, RETIRE_REASON,
                requested=lambda: self.signal.requested() and watcher.held_since is not None
                and watcher.clock() - watcher.held_since < watcher.cap,
            ),
            armed=not self.initially_requested,
        )

        def run():
            try:
                while not self.stop.is_set():
                    requested = self.signal.requested()
                    watcher.step(requested)
                    if watcher.retired:
                        # Keep the event existing until serve.py has finished shutdown.
                        self.stop.wait()
                        break
                    handles = (wintypes.HANDLE * (1 if requested else 2))(
                        *([self.stop.win32_handle] if requested else [self.stop.win32_handle, event])
                    )
                    timeout = int(watcher.poll_interval * 1000) if requested else 0xFFFFFFFF
                    result = kernel32.WaitForMultipleObjects(len(handles), handles, False, timeout)
                    if result == 0xFFFFFFFF:
                        log.warning("Backend retire wait failed (WinError %s)", ctypes.get_last_error())
                        break
            finally:
                if not watcher.retired and not getattr(self.tracker.app.state, "retiring", False):
                    watcher.hold.release()
                kernel32.CloseHandle(event)

        try:
            self.thread = threading.Thread(target=run, name="wg2-backend-retire", daemon=True)
            self.thread.start()
        except BaseException:
            self.thread = None
            kernel32.CloseHandle(event)
            raise

    def close(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=self.tracker.timeout + 1.0)
            if self.thread.is_alive():
                return
        if self.signal is not None:
            self.signal.close()
        self.stop.close()
