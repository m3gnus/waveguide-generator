"""Headless retirement holds submissions while accepted work drains."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from launch.backend_lifetime import ActivityTracker, RetirementWatcher
from launchers.backend_start import backend_argv
from launchers.update_lock import ordinary_path
from server.app import QUIET_REQUEST_ROUTES, create_app
from server.jobs.store import JobStore
from server.platform import paths
from server.updates.restart import RestartApproval

from cad_backends import JobsHarness
from test_cad_manual_solve import _ingest, _request as manual_request
from test_update_transaction_contract import SOLVE_BODY, _post


def _machine(*, busy=False):
    approval = RestartApproval()
    events = []
    now = [0.0]
    client = [busy]
    real_hold = approval.retirement_hold.hold

    def hold(reason):
        events.append("hold")
        real_hold(reason)

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

    asyncio.run(scenario())


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
            assert (json.loads(raw)["lifetime"], json.loads(raw)["retiring"]) == ("standalone", False)
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


def test_manual_cad_solve_refuses_then_accepts_after_release(tmp_path):
    from server.cadlink import api

    harness = JobsHarness(tmp_path)
    try:
        ingest_id, _retained = _ingest(harness)
        approval = RestartApproval()
        request = manual_request(harness)
        request.app.state.update_restart = approval
        request.app.state.retirement_hold = approval.retirement_hold
        approval.retirement_hold.hold("test")
        payload = api.ManualSolveOperationRequest(operationId="manual-1", ingestId=ingest_id)
        response = harness._loop.run(api.post_cad_operation(payload, request))
        error = json.loads(response.body)["error"]
        assert response.status_code == 409
        assert (error["code"], error["retryable"], error["stage"], error["message"]) == (
            "update_restart_pending", True, "submission", approval.retirement_hold.refusal(),
        )
        assert harness.store.list_operations() == []
        approval.approve("v1")
        response = harness._loop.run(api.post_cad_operation(payload, request))
        assert json.loads(response.body)["error"]["message"] == approval.refusal()
        approval.release("test")
        assert approval.retirement_hold.held
        approval.retirement_hold.release()
        response = harness._loop.run(api.post_cad_operation(payload, request))
        assert response.operation.operation_id == "manual-1"
    finally:
        harness.close()


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
