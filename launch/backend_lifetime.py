"""Activity and installer retirement for ensure- and CLI-started backends (headless or contained)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import math
import os
from pathlib import Path
import threading
import time

from server.app import QUIET_REQUEST_ROUTES
from launchers.backend_retire_request import REQUEST_FILENAME, take_request
from server.cadlink.fusion.fusion_status import select_heartbeat
from server.cadlink.intake.returns_folder import selected_workspace_root
from server.platform.instance import StopSignal
from server.solver.beat_runtime.clock import suspend_aware_monotonic
from server.solver.beat_runtime import retire
from server.updates.restart import RetirementHold

log = logging.getLogger("wg.launch")
RETIRE_REASON = "the installer is replacing it"
UPDATE_REASON = "an update is replacing it"
RELEASE_REASON = "Waveguide Generator was closed"
RELEASE_START_MARGIN = 2.0


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
        self.last_start = self.last_activity
        self.last_start_wall = 0.0

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
            self.last_start = self.last_activity
            self.last_start_wall = time.time()
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

    def snapshot(self) -> tuple[bool, float, float]:
        """Read client and activity evidence together on the serving loop."""
        async def check():
            client = await self._has_client()
            return client, self.last_start_wall, self.last_activity

        return self._on_loop(check, on_error=(True, float("inf"), self.clock()))

    def retire_if_idle(
        self, request_stop: Callable[[str], None], reason: str,
        *, requested: Callable[[], bool] = lambda: True,
        stop_started: Callable[[], bool] = lambda: False,
    ) -> bool:
        """Hold-checked commit: re-check, mark retiring, then ask the server to stop.

        ``stop_started`` says whether shutdown has begun. If ``request_stop``
        raises after that point the server is stopping anyway, so ``retiring``
        stays set and no client adopts a dying backend; it is rolled back only
        when shutdown never started.
        """

        async def commit() -> bool:
            if getattr(self.app.state, "retiring", False):
                return True
            if await self._has_client() or not requested():
                return False
            self.app.state.retiring = True
            log.info(
                "Retiring idle %s backend (%s)", getattr(self.app.state, "lifetime", "headless"), reason,
            )
            try:
                request_stop(reason)
            except BaseException:
                try:
                    started = bool(stop_started())
                except Exception:  # noqa: BLE001 - unknown means the stop may be under way
                    started = True
                if not started:
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
        owner: str = "installer",
    ):
        self.hold = hold
        self.has_client = has_client
        self.commit = commit
        self.clock = clock
        self.cap = cap
        self.poll_interval = poll_interval
        self.reason = reason
        self.armed = armed
        self.owner = owner
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
            self.hold.hold(self.reason, owner=self.owner)
            self.held_since = self.clock()
            log.info("Holding new work for backend retirement (%s)", self.reason)
        if self.clock() - self.held_since >= self.cap:
            self._release(f"{self.owner} retirement cap expired")
            return
        try:
            if not self.has_client():
                self.retired = self.commit()
                if self.retired:
                    log.info("Backend %s retirement committed (%s)", self.owner, self.reason)
        except Exception:  # noqa: BLE001 - an uncertain predicate must never retire
            log.exception("Could not check backend clients; keeping retirement hold")

    def _release(self, reason: str) -> None:
        self.hold.release(owner=self.owner)
        self.held_since = None
        self.armed = False
        log.info("Backend retirement cancelled (%s)", reason)


class InstallerRetireThread:
    """Own the named event and park in a kernel wait while it is clear."""

    def __init__(
        self, tracker: ActivityTracker, request_stop: Callable[[str], None], signal: retire.RetireSignal,
        *, initially_requested: bool, stop_started: Callable[[], bool] = lambda: False,
    ):
        self.tracker = tracker
        self.request_stop = request_stop
        self.stop_started = stop_started
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
                stop_started=self.stop_started,
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
                    watcher.hold.release(owner=watcher.owner)
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


def heartbeat_fresh(app) -> bool:
    """Read WGLink presence on the lifetime thread; uncertainty keeps it alive."""
    try:
        roots = {Path(app.state.data_dir).resolve()}
        workspace = selected_workspace_root(app.state)
        if workspace is not None:
            roots.add(workspace.resolve())
        selected = [select_heartbeat(root)[0] for root in roots]
        return any(payload is not None for payload in selected)
    except Exception:  # noqa: BLE001 - a failed presence check must keep the backend alive
        if not getattr(app.state, "_retirement_heartbeat_error_logged", False):
            app.state._retirement_heartbeat_error_logged = True
            log.info("Could not check WGLink heartbeat; keeping backend alive", exc_info=True)
        return True


class IdleRetirement:
    """Suspend-aware idle age with a grace period after a missed tick."""

    def __init__(
        self, *, timeout: float = 1800.0, tick: float = 60.0,
        resume_grace: float = 60.0, clock: Callable[[], float] = suspend_aware_monotonic,
    ):
        self.timeout = timeout
        self.tick = tick
        self.resume_grace = resume_grace
        self.clock = clock
        self.created_at = self.last_present = self.previous_step = clock()
        self.grace_until = self.created_at

    @property
    def reason(self) -> str:
        if self.timeout >= 60:
            duration = f"{self.timeout / 60:g} minutes"
        else:
            duration = f"{self.timeout:g} seconds"
        return f"it has had no client for {duration}"

    def step(self, *, has_client: bool, heartbeat_fresh: bool, last_activity: float) -> bool:
        now = self.clock()
        if now - self.previous_step > max(2 * self.tick, self.tick + 30):
            self.grace_until = now + self.resume_grace
            log.info("Backend resumed; granting %g seconds of idle retirement grace", self.resume_grace)
        self.previous_step = now
        if has_client or heartbeat_fresh:
            self.last_present = now
        age = now - max(self.last_present, last_activity, self.created_at)
        return not has_client and not heartbeat_fresh and age >= self.timeout and now >= self.grace_until


class ReleaseRequest:
    """A quitting window releases its backend unless a new client starts."""

    def __init__(self, request, observed_at: float):
        self.requested_at = request["requested_at"]
        self.observed_at = observed_at
        self.wait_logged = False

    def step(self, *, now, has_client, last_start_wall, last_activity) -> str:
        if not math.isfinite(last_start_wall):
            return "cancel:activity could not be checked"
        # The quitting SPA's keepalive unload flushes can start just after Quit.
        # Let them finish and reach three seconds of quiet without cancelling.
        if last_start_wall > self.requested_at + RELEASE_START_MARGIN:
            return "cancel:a new client started"
        if has_client:
            if now - self.observed_at >= 10:
                return "cancel:a client is still connected or it still holds work"
            return "wait"
        return "commit" if now - last_activity >= 3 else "wait"


class _RequestNotification:
    """Windows directory notification, waited alongside our own stop handle."""

    def __init__(self, directory: Path, stop: StopSignal):
        import ctypes
        from ctypes import wintypes

        self.wintypes = wintypes
        self.stop = stop
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "FindFirstChangeNotificationW": ((wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
            "FindNextChangeNotification": ((wintypes.HANDLE,), wintypes.BOOL),
            "FindCloseChangeNotification": ((wintypes.HANDLE,), wintypes.BOOL),
            "WaitForMultipleObjects": (
                (wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD), wintypes.DWORD,
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.kernel.FindFirstChangeNotificationW(str(directory), False, 0x01 | 0x10 | 0x08)
        invalid = ctypes.c_void_p(-1).value
        if not self.handle or self.handle == invalid:
            self.handle = None
            raise OSError("Could not create backend request notification")

    def wait(self, timeout: float) -> None:
        handles = (self.wintypes.HANDLE * 2)(self.stop.win32_handle, self.handle)
        result = self.kernel.WaitForMultipleObjects(2, handles, False, max(1, math.ceil(timeout * 1000)))
        if result == 1:
            if not self.kernel.FindNextChangeNotification(self.handle):
                raise OSError("Could not rearm backend request notification")
        elif result not in (0, 0x102):
            raise OSError("Backend request notification wait failed")

    def close(self) -> None:
        if self.handle:
            self.kernel.FindCloseChangeNotification(self.handle)
            self.handle = None


class BackendLifetimeThread:
    """File requests and idle retirement for both detached and contained backends."""

    def __init__(
        self, tracker: ActivityTracker, request_stop: Callable[[str], None], *,
        locks_dir: Path, idle_timeout: float = 1800.0, started_at_wall: float,
        poll_interval: float = 5.0, notify: str = "auto",
        stop_started: Callable[[], bool] = lambda: False,
    ):
        self.tracker = tracker
        self.request_stop = request_stop
        self.stop_started = stop_started
        self.locks_dir = Path(locks_dir)
        self.started_at_wall = started_at_wall
        self.poll_interval = poll_interval
        self.notify = notify
        self.tick = min(60.0, max(0.25, idle_timeout / 4))
        self.clock = tracker.clock
        self.idle = IdleRetirement(timeout=idle_timeout, tick=self.tick, clock=self.clock)
        self.release: ReleaseRequest | None = None
        self.update: RetirementWatcher | None = None
        self._request_pending = False
        self.stop = StopSignal()
        self.thread: threading.Thread | None = None

    def _take_request(self) -> None:
        # Counterpart: launchers/statusapp/controller.py writes only this file,
        # and never opens, signals, kills or waits for the adopted backend pid.
        request = take_request(self.locks_dir, pid=os.getpid(), started_at_wall=self.started_at_wall)
        self._request_pending = request is None and os.path.lexists(self.locks_dir / REQUEST_FILENAME)
        if request is None:
            return
        if self.update is not None or (self.release is not None and request["mode"] == "release"):
            log.info("Backend retirement request ignored (a request is already active)")
            return
        log.info("Backend retirement request taken (%s, %s)", request["mode"], request["reason"])
        if request["mode"] == "release":
            self.release = ReleaseRequest(request, self.clock())
        else:
            self.release = None
            watcher = RetirementWatcher(
                self.tracker.app.state.retirement_hold, self.tracker.has_client,
                lambda: self.tracker.retire_if_idle(
                    self.request_stop, UPDATE_REASON,
                    requested=lambda: watcher.held_since is not None
                    and watcher.clock() - watcher.held_since < watcher.cap and not self.stop.is_set(),
                    stop_started=self.stop_started,
                ),
                clock=self.clock, owner="update", reason=UPDATE_REASON,
            )
            self.update = watcher
            watcher.step(True)  # Hold new submissions as soon as the file is taken.

    def _commit(self, reason: str, owner: str, requested: Callable[[], bool]) -> bool:
        hold = self.tracker.app.state.retirement_hold
        hold.hold(reason, owner=owner)
        committed = self.tracker.retire_if_idle(
            self.request_stop, reason, requested=lambda: not self.stop.is_set() and requested(),
            stop_started=self.stop_started,
        )
        if not committed and not getattr(self.tracker.app.state, "retiring", False):
            hold.release(owner=owner)
        elif committed:
            source = "release" if owner == "gui-quit" else "idle retirement"
            log.info("Backend %s committed (%s)", source, reason)
        return committed

    def _step(self, *, idle_tick: bool) -> None:
        if not idle_tick and self.update is None and self.release is None:
            return
        if self.update is not None:
            if idle_tick:
                client, _last_start_wall, last_activity = self.tracker.snapshot()
                self.idle.step(has_client=client, heartbeat_fresh=False, last_activity=last_activity)
            self.update.step(True)
            if not self.update.armed:
                self.update = None
            return
        client, last_start_wall, last_activity = self.tracker.snapshot()
        if self.release is not None:
            if idle_tick:
                self.idle.step(has_client=client, heartbeat_fresh=False, last_activity=last_activity)
            release = self.release
            outcome = release.step(
                now=self.clock(), has_client=client,
                last_start_wall=last_start_wall, last_activity=last_activity,
            )
            if outcome.startswith("cancel:"):
                log.info("Backend release cancelled (%s)", outcome.removeprefix("cancel:"))
                self.release = None
            elif outcome == "commit":
                committed = self._commit(
                    RELEASE_REASON, "gui-quit",
                    lambda: self.tracker.last_start_wall <= release.requested_at + RELEASE_START_MARGIN,
                )
                if committed:
                    self.release = None
            elif not release.wait_logged:
                log.info("Backend release waiting for clients and three seconds of quiet")
                release.wait_logged = True
            return
        if idle_tick and self.idle.step(
            has_client=client, heartbeat_fresh=heartbeat_fresh(self.tracker.app), last_activity=last_activity,
        ):
            self._commit(self.idle.reason, "idle", lambda: self.tracker.last_activity == last_activity)

    def _wait(self, timeout: float, notification: _RequestNotification | None) -> None:
        if notification is None:
            self.stop.wait(timeout)
        else:
            notification.wait(timeout)

    def _run(self) -> None:
        notification = None
        try:
            if os.name == "nt" and self.notify != "poll" and self.stop.win32_handle:
                try:
                    notification = _RequestNotification(self.locks_dir, self.stop)
                except OSError:
                    log.info("Backend request notifications unavailable; polling instead")
            next_idle = self.clock()
            while not self.stop.is_set():
                self._take_request()  # Includes the initial stale-file sweep.
                if getattr(self.tracker.app.state, "retiring", False):
                    self.stop.wait()
                    break
                now = self.clock()
                idle_tick = now >= next_idle
                self._step(idle_tick=idle_tick)
                if idle_tick:
                    next_idle = self.clock() + self.tick
                if getattr(self.tracker.app.state, "retiring", False):
                    self.stop.wait()
                    break
                delay = max(0.0, next_idle - self.clock())
                if notification is None:
                    delay = min(delay, self.poll_interval)
                if self.release is not None or self.update is not None:
                    delay = min(delay, 0.25)
                if self._request_pending:
                    delay = min(delay, 1.0)
                try:
                    self._wait(delay, notification)
                except OSError:
                    log.info("Backend request notification failed; polling instead")
                    notification.close()
                    notification = None
        except Exception:  # noqa: BLE001 - lifetime monitoring must never crash the backend
            log.exception("Backend lifetime monitor failed; keeping backend alive")
        finally:
            if notification is not None:
                notification.close()
            if not getattr(self.tracker.app.state, "retiring", False):
                for owner in ("update", "gui-quit", "idle"):
                    self.tracker.app.state.retirement_hold.release(owner=owner)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="wg2-backend-lifetime", daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.thread = None
            self.stop.close()
            raise

    def close(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=self.tracker.timeout + 1)
            if self.thread.is_alive():
                return
        self.stop.close()
