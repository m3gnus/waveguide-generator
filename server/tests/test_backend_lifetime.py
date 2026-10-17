"""Headless retirement holds submissions while accepted work drains."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from launch.backend_lifetime import ActivityTracker, InstallerRetireThread, RetirementWatcher
from launchers.backend_start import backend_argv
from launchers.install_paths import ordinary_path
from server.app import QUIET_REQUEST_ROUTES, create_app
from server.jobs.store import JobStore
from server.platform import paths
from server.updates.restart import RestartApproval

from server.tests._asgi import SOLVE_BODY, _post


def _machine(*, busy=False):
    approval = RestartApproval()
    events = []
    now = [0.0]
    client = [busy]
    real_hold = approval.retirement_hold.hold

    def hold(reason, owner="installer"):
        events.append("hold")
        real_hold(reason, owner=owner)

    approval.retirement_hold.hold = hold

    def has_client():
        events.append("check")
        return client[0]

    def commit():
        # The production commit re-checks on the serving loop before setting state.
        if has_client():
            return False
        events.extend(("retiring", "request_stop"))
        return True

    watcher = RetirementWatcher(approval.retirement_hold, has_client, commit, clock=lambda: now[0])
    return watcher, approval.retirement_hold, events, now, client


def test_idle_event_holds_then_rechecks_before_retiring():
    watcher, hold, events, _now, _client = _machine()
    watcher.step(True)
    assert events == ["hold", "check", "check", "retiring", "request_stop"]
    assert hold.held and watcher.retired
    watcher.step(False)
    assert hold.held  # A committed stop cannot reopen submissions.


def test_busy_event_keeps_hold_until_client_leaves():
    watcher, hold, events, _now, client = _machine(busy=True)
    watcher.step(True)
    watcher.step(True)
    assert hold.held and events == ["hold", "check", "check"]
    client[0] = False
    watcher.step(True)
    assert watcher.retired and events[-2:] == ["retiring", "request_stop"]


def test_reset_releases_and_a_clear_event_rearms():
    watcher, hold, events, _now, _client = _machine(busy=True)
    watcher.step(True)
    watcher.step(False)
    assert not hold.held and not watcher.retired and watcher.armed
    assert "request_stop" not in events
    watcher.step(True)
    assert hold.held and events.count("hold") == 2


def test_cap_disarms_until_the_event_is_seen_clear():
    watcher, hold, events, now, _client = _machine(busy=True)
    watcher.step(True)
    now[0] = 130.0
    watcher.step(True)
    assert not hold.held and not watcher.armed
    for _ in range(3):
        watcher.step(True)
    assert events.count("hold") == 1 and "request_stop" not in events
    watcher.step(False)
    watcher.step(True)
    assert hold.held and events.count("hold") == 2


def test_predicate_error_keeps_hold():
    watcher, hold, events, _now, _client = _machine()

    def failed():
        raise RuntimeError("activity unavailable")

    watcher.has_client = failed
    watcher.step(True)
    assert hold.held and "request_stop" not in events


@pytest.mark.parametrize("scope, counted", [
    ({"type": "http", "method": "GET", "path": "/health"}, False),
    *[({"type": "http", "method": method, "path": path}, False) for method, path in QUIET_REQUEST_ROUTES],
    ({"type": "http", "method": "POST", "path": "/api/solve"}, True),
])
@pytest.mark.parametrize("finish", ["normal", "exception", "disconnect"])
def test_http_activity_is_counted_until_finally(scope, counted, finish):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        clock = [0.0]

        async def app(_scope, _receive, _send):
            entered.set()
            await release.wait()
            if finish == "exception":
                raise ValueError("request failed")

        tracker = ActivityTracker(app, clock=lambda: clock[0])
        clock[0] = 1.0
        task = asyncio.create_task(tracker(scope, None, None))
        await entered.wait()
        assert tracker.loop is asyncio.get_running_loop()
        assert tracker.http_requests == int(counted)
        assert tracker.last_activity == (1.0 if counted else 0.0)
        clock[0] = 2.0
        release.set()
        if finish == "disconnect":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif finish == "exception":
            with pytest.raises(ValueError):
                await task
        else:
            await task
        assert tracker.http_requests == 0
        assert tracker.last_activity == (2.0 if counted else 0.0)

    asyncio.run(scenario())


def test_websocket_is_a_client_until_disconnect():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def app(_scope, _receive, _send):
            entered.set()
            await release.wait()

        app.state = SimpleNamespace(jobs_runtime=None)
        tracker = ActivityTracker(app)
        task = asyncio.create_task(tracker({"type": "websocket"}, None, None))
        await entered.wait()
        assert tracker.websockets == 1 and await tracker._has_client()
        assert not await tracker.holds_work()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert tracker.websockets == 0 and not await tracker._has_client()

    asyncio.run(scenario())


@pytest.mark.parametrize("status, claimed, manual, expected", [
    ("queued", False, False, True), ("running", True, False, True),
    ("preparing", False, False, True), ("preparing", True, False, True),
    ("preparing", False, True, True), ("complete", False, False, False),
    ("error", False, False, False), ("cancelled", False, False, False),
])
def test_every_active_store_row_holds_work(tmp_path, status, claimed, manual, expected):
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    try:
        store.create_job({
            "id": "job", "status": status,
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
            "queued_at": "2026-01-01T00:00:00", "started_at": "2026-01-01T00:00:01" if claimed else None,
            "config_json": {}, "config_summary_json": {},
            "task_metadata": {"cad": {"manual_waiting": manual}},
        })
        runtime = SimpleNamespace(store=store, running_job_ids=())
        tracker = ActivityTracker(SimpleNamespace(state=SimpleNamespace(jobs_runtime=runtime)))
        assert asyncio.run(tracker.holds_work()) is expected
    finally:
        store.close()


@pytest.mark.parametrize("field", ["running_job_ids", "_prep_running", "_prep_queue"])
def test_runtime_lanes_hold_work_without_store_reads(field):
    runtime = SimpleNamespace(running_job_ids=())
    setattr(runtime, field, ("job",))
    tracker = ActivityTracker(SimpleNamespace(state=SimpleNamespace(jobs_runtime=runtime)))
    assert asyncio.run(tracker.holds_work())


@pytest.mark.parametrize("delivery", ["absent", "idle", "active", "error"])
def test_delivery_activity_holds_work(delivery):
    state = SimpleNamespace(jobs_runtime=None)

    def in_flight():
        if delivery == "error":
            raise RuntimeError("delivery state unavailable")
        return delivery == "active"

    if delivery != "absent":
        state.cad_delivery_in_flight = in_flight
    tracker = ActivityTracker(SimpleNamespace(state=state))
    assert asyncio.run(tracker.holds_work()) is (delivery in {"active", "error"})


@pytest.mark.parametrize("pass_fails", [False, True])
def test_delivery_loop_honours_hold_and_counts_in_flight_work(tmp_path, monkeypatch, pass_fails):
    from server.cadlink import api

    async def scenario():
        hold = RestartApproval().retirement_hold
        hold.hold("test")
        state = SimpleNamespace(jobs_runtime=None, retirement_hold=hold)
        app = SimpleNamespace(state=state)
        tracker = ActivityTracker(app)
        sleeping, resume = asyncio.Queue(), asyncio.Queue()
        collecting, collect_release = asyncio.Event(), asyncio.Event()
        entered, pass_release = asyncio.Event(), asyncio.Event()
        operation_entered, operation_release = asyncio.Event(), asyncio.Event()
        passes = []

        async def sleep(_seconds):
            sleeping.put_nowait(True)
            await resume.get()

        async def collect(function, *args):
            assert state.cad_delivery_in_flight()
            collecting.set()
            await collect_release.wait()
            return function(*args)

        async def operation():
            operation_entered.set()
            await operation_release.wait()

        async def delivery_pass(_context, *, spawn, **_kwargs):
            passes.append(True)
            entered.set()
            await pass_release.wait()
            if pass_fails:
                raise RuntimeError("pass failed")
            spawn("operation", operation())

        api_asyncio = SimpleNamespace(**vars(asyncio))
        api_asyncio.sleep = sleep
        api_asyncio.to_thread = collect
        monkeypatch.setattr(api, "asyncio", api_asyncio)
        monkeypatch.setenv("WG2_CAD_DELIVERY", "1")
        monkeypatch.setattr(api, "selected_workspace_root", lambda _state: tmp_path)
        monkeypatch.setattr(api, "addin_inbox_session_signature", lambda _root: None)
        monkeypatch.setattr(api, "_preparation_context", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(api, "run_delivery_pass", delivery_pass)
        await api._deliver_solve_commands(app)()
        task = state.cad_delivery_task
        try:
            for _ in range(2):
                await asyncio.wait_for(sleeping.get(), 2)
                assert not passes and not state.cad_delivery_in_flight()
                assert not collecting.is_set()
                resume.put_nowait(True)
            await asyncio.wait_for(sleeping.get(), 2)
            hold.release()
            resume.put_nowait(True)
            await asyncio.wait_for(collecting.wait(), 2)
            assert state.cad_delivery_in_flight() and await tracker.holds_work()
            collect_release.set()
            await asyncio.wait_for(entered.wait(), 2)
            assert passes == [True]
            assert state.cad_delivery_in_flight() and await tracker.holds_work()
            hold.hold("test")
            pass_release.set()
            await asyncio.wait_for(sleeping.get(), 2)
            if not pass_fails:
                await asyncio.wait_for(operation_entered.wait(), 2)
                assert state.cad_delivery_in_flight() and await tracker.holds_work()
                operation_release.set()
                await asyncio.gather(*state.cad_preparations)
            assert not state.cad_delivery_in_flight()
            resume.put_nowait(True)
            await asyncio.wait_for(sleeping.get(), 2)
            assert passes == [True]
        finally:
            task.cancel()
            operations = list(getattr(state, "cad_preparations", ()))
            for pending in operations:
                pending.cancel()
            await asyncio.gather(task, *operations, return_exceptions=True)
        assert not state.cad_delivery_in_flight()

    asyncio.run(scenario())


def test_loop_timeout_and_error_fail_closed():
    async def scenario():
        tracker = ActivityTracker(SimpleNamespace(state=SimpleNamespace(jobs_runtime=None)), timeout=0.01)
        tracker.loop = asyncio.get_running_loop()
        assert not await asyncio.to_thread(tracker.has_client)

        async def failed():
            raise ValueError("store read failed")

        tracker._has_client = failed
        assert await asyncio.to_thread(tracker.has_client)
        assert not await asyncio.to_thread(tracker.retire_if_idle, lambda _: pytest.fail("must not stop"), "test")

        async def stalled():
            await asyncio.Event().wait()

        tracker._has_client = stalled
        assert await asyncio.to_thread(tracker.has_client)

    asyncio.run(scenario())


def test_commit_marks_retiring_before_requesting_stop():
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace(jobs_runtime=None, retiring=False))
        tracker = ActivityTracker(app)
        tracker.loop = asyncio.get_running_loop()
        stops = []
        assert await asyncio.to_thread(
            tracker.retire_if_idle, lambda reason: stops.append((app.state.retiring, reason)), "test"
        )
        assert stops == [(True, "test")]
        tracker.http_requests = 1
        assert await asyncio.to_thread(tracker.retire_if_idle, stops.append, "again", requested=lambda: False)
        assert stops == [(True, "test")]

    asyncio.run(scenario())


def test_commit_rolls_back_retiring_when_stop_raises():
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace(jobs_runtime=None, retiring=False))
        tracker = ActivityTracker(app)
        stops = []

        def failed_stop(reason):
            stops.append(reason)
            assert app.state.retiring
            raise RuntimeError("stop failed")

        # Run the actual commit coroutine directly so its raised exception is visible.
        tracker._on_loop = lambda check, **_kwargs: check()
        with pytest.raises(RuntimeError, match="stop failed"):
            await tracker.retire_if_idle(failed_stop, "test")
        assert not app.state.retiring and stops == ["test"]

    asyncio.run(scenario())


@pytest.mark.parametrize("started, kept", [
    (lambda: True, True),
    (lambda: False, False),
    # Not knowing whether shutdown began counts as begun.
    (lambda: (_ for _ in ()).throw(RuntimeError("unknown")), True),
])
def test_retiring_survives_a_failed_stop_once_shutdown_began(started, kept):
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace(jobs_runtime=None, retiring=False))
        tracker = ActivityTracker(app)

        def failed_stop(_reason):
            # serve.py sets should_exit first; the budget's thread start then fails.
            raise RuntimeError("could not start the shutdown budget")

        tracker._on_loop = lambda check, **_kwargs: check()
        with pytest.raises(RuntimeError, match="shutdown budget"):
            await tracker.retire_if_idle(failed_stop, "test", stop_started=started)
        assert app.state.retiring is kept

    asyncio.run(scenario())


@pytest.mark.parametrize("result", [False, True])
def test_loop_returns_completed_result_after_timeout(monkeypatch, result):
    from concurrent.futures import Future

    class TimedOutFuture(Future):
        def result(self, timeout=None):
            if timeout is not None:
                self.set_result(result)
                raise TimeoutError
            return super().result()

    future = TimedOutFuture()

    def submit(coroutine, _loop):
        coroutine.close()
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)
    tracker = ActivityTracker(SimpleNamespace(state=SimpleNamespace(jobs_runtime=None)))
    tracker.loop = SimpleNamespace(is_running=lambda: True)
    assert tracker._on_loop(tracker._has_client, on_error=not result) is result
    assert not future.cancelled()


@pytest.fixture
def retire_thread_driver(monkeypatch, retire_event):
    import ctypes
    from launch import backend_lifetime
    from server.solver.beat_runtime import retire

    def drive(requests, *, retiring=False, wait_failure=False, initially_requested=None, start_failure=False):
        requests = list(requests)
        hold = RestartApproval().retirement_hold
        if retiring:
            hold.hold("test")
        state = SimpleNamespace(retirement_hold=hold, retiring=retiring)
        stops, observations, closed = [], [], []
        signal = SimpleNamespace(
            error=None, requested=lambda: requests[0], close=lambda: closed.append("signal"),
        )

        class Stop(threading.Event):
            win32_handle = 10

            def close(self):
                closed.append("stop")

        class InlineThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                if start_failure:
                    raise RuntimeError("thread start failed")
                self.target()

            def join(self, **_kwargs):
                assert not start_failure, "cannot join an unstarted thread"

            def is_alive(self):
                return False

        def open_event(_access, _inherit, name):
            assert name == retire_event
            return 11

        def wait(_count, _handles, _all, _timeout):
            observations.append((requests[0], hold.held, len(stops)))
            if wait_failure:
                return 0xFFFFFFFF
            assert len(requests) > 1, "watcher did not retire after the clear/set sequence"
            requests.pop(0)
            return 0

        kernel32 = SimpleNamespace(
            OpenEventW=open_event, WaitForMultipleObjects=wait,
            CloseHandle=lambda _event: closed.append("wait event"),
        )
        monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
        monkeypatch.setattr(backend_lifetime, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(backend_lifetime, "StopSignal", Stop)
        monkeypatch.setattr(backend_lifetime, "threading", SimpleNamespace(Thread=InlineThread))
        monkeypatch.setattr(retire, "RetireSignal", lambda: pytest.fail("must use the early signal"))

        def commit(request_stop, reason, *, requested, stop_started):
            assert requested()
            state.retiring = True
            request_stop(reason)
            worker.stop.set()
            return True

        tracker = SimpleNamespace(
            app=SimpleNamespace(state=state), has_client=lambda: retiring,
            retire_if_idle=commit, timeout=0.01,
        )
        if initially_requested is None:
            initially_requested = signal.requested()
        worker = InstallerRetireThread(tracker, stops.append, signal, initially_requested=initially_requested)
        if start_failure:
            with pytest.raises(RuntimeError, match="thread start failed"):
                worker.start()
        else:
            worker.start()
        worker.close()
        assert closed == ["wait event", "signal", "stop"]
        return hold, stops, observations

    return drive


def test_initially_set_event_is_disarmed_until_clear(retire_thread_driver):
    hold, stops, observations = retire_thread_driver([True, True, False, True])
    assert observations == [(True, False, 0), (True, False, 0), (False, False, 0)]
    assert hold.held and len(stops) == 1


def test_event_set_after_creation_is_honoured(retire_thread_driver):
    hold, stops, observations = retire_thread_driver([True], initially_requested=False)
    assert hold.held and len(stops) == 1 and not observations


def test_retire_thread_start_failure_closes_early_signal(retire_thread_driver):
    hold, stops, observations = retire_thread_driver([False], start_failure=True)
    assert not hold.held and not stops and not observations


def test_retire_thread_does_not_release_hold_when_retiring(retire_thread_driver):
    hold, stops, observations = retire_thread_driver([True], retiring=True, wait_failure=True)
    assert hold.held and not stops
    assert observations == [(True, True, 0)]


@pytest.mark.parametrize("lifetime", ["headless", "contained"])
@pytest.mark.parametrize("initially_set", [False, True])
@pytest.mark.parametrize("failure", [None, "interface", "port", "temporary", "app", "thread start"])
def test_serve_creates_and_passes_early_signal(tmp_path, monkeypatch, retire_event, initially_set, failure, lifetime):
    from launch import backend_lifetime, serve
    from server.solver.beat_runtime import retire
    from server.solver.beat_runtime.clock import suspend_aware_monotonic

    arguments = serve.build_parser().parse_args(["--lifetime=headless", "--no-browser"])
    monkeypatch.setattr(serve, "build_parser", lambda: SimpleNamespace(parse_args=lambda _argv: arguments))

    events = []
    requested = [initially_set]
    signal = SimpleNamespace(
        requested=lambda: requested[0], close=lambda: events.append("signal close"),
    )

    def create_signal():
        assert retire.RETIRE_IDLE_EVENT == retire_event
        events.append("signal create")
        return signal

    def fail_at(stage):
        events.append(stage)
        if failure == stage:
            raise OSError(stage)

    class Thread:
        def __init__(self, tracker, request_stop, early_signal, *, initially_requested, stop_started):
            assert early_signal is signal and initially_requested is initially_set
            # A contained CLI backend answers the installer exactly as a headless one.
            assert tracker.app.state.lifetime == lifetime
            assert stop_started() is False
            events.append("thread create")

        def start(self):
            fail_at("thread start")

        def close(self):
            events.append("thread close")
            signal.close()

    class LifetimeThread:
        def __init__(self, tracker, request_stop, **kwargs):
            assert tracker.clock is suspend_aware_monotonic
            assert kwargs["locks_dir"] == tmp_path and kwargs["idle_timeout"] == 1800
            assert isinstance(kwargs["started_at_wall"], float)
            events.append("lifetime create")

        def start(self):
            events.append("lifetime start")

        def close(self):
            events.append("lifetime close")

    def reserve(*_args, **_kwargs):
        fail_at("port")
        return SimpleNamespace(close=lambda: events.append("listener close")), 3100

    def create_app(**_kwargs):
        fail_at("app")
        return SimpleNamespace(state=SimpleNamespace(jobs_runtime=None))

    def run(**_kwargs):
        events.append("run")

    monkeypatch.setattr(retire, "RetireSignal", create_signal)
    monkeypatch.setattr(backend_lifetime, "InstallerRetireThread", Thread)
    environ = {"WG2_BACKEND_CONTAINED": "1"} if lifetime == "contained" else {}
    monkeypatch.setattr(backend_lifetime, "BackendLifetimeThread", LifetimeThread)
    monkeypatch.setattr(serve, "os", SimpleNamespace(name="nt", environ=environ, getpid=lambda: 123))
    monkeypatch.setattr(serve, "ensure_data_layout", lambda: SimpleNamespace(root=tmp_path, locks=tmp_path))
    for name in ("setup_logging", "flush_logs", "_start_beat_cpu_provisioning",
                 "harden_console", "_publish_status_ready"):
        monkeypatch.setattr(serve, name, lambda *_args: None)
    monkeypatch.setattr(serve, "_release_interface_error", lambda: "interface" if failure == "interface" else None)
    monkeypatch.setattr(serve, "_start_temporary_session", lambda: fail_at("temporary"))
    monkeypatch.setattr(serve, "InstanceLock", lambda *_args: SimpleNamespace(
        acquire=lambda *_args: events.append("lock acquire"), update_port=lambda *_args: None,
        release=lambda: events.append("lock release"),
    ))
    monkeypatch.setattr(serve, "reserve_port", reserve)
    monkeypatch.setattr(serve, "create_app", create_app)
    monkeypatch.setattr(serve.uvicorn, "Config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve.uvicorn, "Server", lambda _config: SimpleNamespace(run=run, should_exit=False))
    monkeypatch.setattr(serve, "ShutdownBackstop", lambda: SimpleNamespace(
        activate=lambda: None, deactivate=lambda: None, begun=False,
    ))
    if failure == "temporary":
        with pytest.raises(OSError, match="temporary"):
            serve.main(["--lifetime=headless", "--no-browser"])
    else:
        assert serve.main(["--lifetime=headless", "--no-browser"]) == (0 if failure is None else 1)
    assert events[0] == "lock acquire"
    if failure in {"interface", "port", "temporary"}:
        # These exits come before the event exists, so there is nothing to close.
        assert "signal create" not in events and "signal close" not in events
        return
    # Created after the cheap early exits and before the slow app build.
    assert events.index("temporary") < events.index("signal create") < events.index("app")
    assert events.count("signal close") == 1
    if failure is None:
        assert events.index("app") < events.index("thread create")
        assert "run" in events and "thread close" in events
        assert events.index("lifetime start") < events.index("run")
        assert events.index("lifetime close") < events.index("lock release")


def test_retirement_release_wakes_listeners_without_update_bookkeeping():
    approval = RestartApproval()
    listeners, observers = [], []
    approval.add_release_listener(lambda *args: listeners.append(args))
    approval.add_release_observer(observers.append)
    hold = approval.retirement_hold
    hold.hold("test")
    assert hold.refusal() == "This backend is shutting down (test); submit again and a new one will start."
    assert approval.pending is None and approval.refusal() is None
    assert approval.admit(lambda: "started") == (True, "started")
    hold.release()
    assert len(listeners) == 1 and observers == []
    hold.release()
    assert len(listeners) == 1


@pytest.mark.parametrize("update_first", [True, False])
def test_retirement_and_update_release_are_independent(update_first):
    approval = RestartApproval()
    hold = approval.retirement_hold
    if update_first:
        approval.approve("v1")
    hold.hold("test")
    if not update_first:
        approval.approve("v1")
    assert approval.pending == "v1" and approval.admit(lambda: None) == (False, None)
    message = approval.refusal()
    if update_first:
        hold.release()
        assert approval.refusal() == message
        approval.release("test")
    else:
        approval.release("test")
        assert hold.held and approval.refusal() is None
        assert approval.admit(lambda: True) == (True, True)
        hold.release()


def test_health_defaults_and_submission_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario():
        app = create_app(data_dir=tmp_path / "data", solver_warmup=False)
        runtime = app.state.jobs_runtime
        try:
            status, raw = await _post(app, "/health", None, method="GET")
            assert status == 200
            assert (json.loads(raw)["lifetime"], json.loads(raw)["retiring"]) == ("attached", False)
            hold = app.state.retirement_hold
            hold.hold("test")
            for path, body in (("/api/solve", SOLVE_BODY), ("/api/cadlink/ingest", {})):
                status, raw = await _post(app, path, body)
                assert status == 409
                error = json.loads(raw)["error"]
                assert (error["code"], error["retryable"], error["stage"], error["message"]) == (
                    "update_restart_pending", True, "submission", hold.refusal(),
                )
            app.state.update_restart.approve("v1")
            for path, body in (("/api/solve", SOLVE_BODY), ("/api/cadlink/ingest", {})):
                status, raw = await _post(app, path, body)
                assert status == 409
                assert json.loads(raw)["error"]["message"] == app.state.update_restart.refusal()
            app.state.update_restart.release("test")
            hold.release()
            status, _raw = await _post(app, "/api/solve", SOLVE_BODY)
            assert status == 200
            status, raw = await _post(app, "/api/cadlink/ingest", {})
            assert status == 422  # Reaches body validation after the middleware releases.
        finally:
            await runtime.wait_idle()
            await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", [
    r"\\?\C:\Program Files\WG\python.exe", r"\\?\UNC\server\share\python.exe",
    r"C:\plain\python.exe", r"\\server\share\python.exe", r"\\?\odd",
    r"\\?\UNC\server", r"\\?\UNC\\share", r"\\?\1:\odd", "", "/tmp/python",
])
@pytest.mark.parametrize("os_name", ["nt", "posix"])
def test_display_path_agrees_with_launcher(monkeypatch, name, os_name):
    with monkeypatch.context() as patch:
        patch.setattr(paths.os, "name", os_name)
        displayed, ordinary = paths.display_path(name), ordinary_path(name)
    assert displayed == ordinary


def test_lifetime_flag_is_opt_in_and_hidden():
    from launch.serve import build_parser

    assert "--lifetime" not in build_parser().format_help()
    assert "--lifetime=headless" in backend_argv(port=None, headless=True)
    assert "--lifetime=headless" not in backend_argv(port=None)


@pytest.mark.parametrize("name", [
    "", "installer-event", "Global\\event", "WaveguideGeneratorTestRetireIdleBeatHosts-",
    # The production name must be refused before any event is created.
    "WaveguideGeneratorRetireIdleBeatHosts",
])
def test_tests_only_entry_refuses_unsafe_event_names(monkeypatch, name):
    from server.tests.backend_lifetime_main import main

    monkeypatch.setenv("WG2_TEST_RETIRE_EVENT", name)
    with pytest.raises(ValueError, match="private test retire event"):
        main([])


def test_installer_reset_during_client_check_prevents_commit():
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace(jobs_runtime=None, retiring=False))
        tracker = ActivityTracker(app)
        tracker.loop = asyncio.get_running_loop()
        requested = [True]

        async def client_check():
            requested[0] = False
            return False

        tracker._has_client = client_check
        assert not await asyncio.to_thread(
            tracker.retire_if_idle, lambda _: pytest.fail("event was reset"), "test", requested=lambda: requested[0],
        )
        assert app.state.retiring is False

    asyncio.run(scenario())


def test_websocket_blocks_the_retirement_machine():
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace(jobs_runtime=None, retiring=False))
        tracker = ActivityTracker(app)
        tracker.loop = asyncio.get_running_loop()
        tracker.websockets = 1
        approval = RestartApproval()
        stops = []
        watcher = RetirementWatcher(
            approval.retirement_hold, tracker.has_client,
            lambda: tracker.retire_if_idle(stops.append, "test"),
        )
        await asyncio.to_thread(watcher.step, True)
        assert approval.retirement_hold.held and not stops and not app.state.retiring
        tracker.websockets = 0
        await asyncio.to_thread(watcher.step, True)
        assert stops == ["test"] and app.state.retiring

    asyncio.run(scenario())


@pytest.mark.parametrize("flag, marker, expected", [
    (None, None, "attached"),
    ("headless", None, "headless"),
    ("headless", "1", "contained"),
    (None, "1", "contained"),
    ("headless", "0", "headless"),
])
def test_serve_reports_one_lifetime(flag, marker, expected):
    from launch import serve

    args = SimpleNamespace(lifetime=flag)
    environ = {} if marker is None else {"WG2_BACKEND_CONTAINED": marker}
    assert serve._backend_lifetime(args, environ) == expected
    # Both lifetimes nobody owns answer the installer's retire request.
    assert (expected in serve.RETIRABLE_LIFETIMES) is (expected != "attached")
