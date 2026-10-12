"""In-process retirement machines, file protocol, and lifetime thread waits."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from launch import backend_lifetime as lifetime
from launch.backend_lifetime import ActivityTracker, BackendLifetimeThread, IdleRetirement, ReleaseRequest
from launchers import backend_retire_request as files
from server.updates.restart import RestartApproval


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_idle_timeout_and_reason():
    clock = Clock()
    idle = IdleRetirement(timeout=1800, clock=clock)
    assert idle.reason == "it has had no client for 30 minutes"
    for second in range(0, 1800, 60):
        clock.now = second
        assert not idle.step(has_client=False, heartbeat_fresh=False, last_activity=0)
    clock.now = 1800
    assert idle.step(has_client=False, heartbeat_fresh=False, last_activity=0)


@pytest.mark.parametrize("presence", ["client", "websocket", "activity", "heartbeat"])
def test_presence_restarts_idle_count(presence):
    clock = Clock()
    idle = IdleRetirement(timeout=10, tick=1, clock=clock)
    clock.now = 9
    activity = 9 if presence == "activity" else 0
    assert not idle.step(
        has_client=presence in ("client", "websocket"),
        heartbeat_fresh=presence == "heartbeat", last_activity=activity,
    )
    clock.now = 18
    assert not idle.step(has_client=False, heartbeat_fresh=False, last_activity=activity)
    clock.now = 19
    assert idle.step(has_client=False, heartbeat_fresh=False, last_activity=activity)


def test_fresh_heartbeat_keeps_backend_indefinitely():
    clock = Clock()
    idle = IdleRetirement(timeout=10, tick=5, clock=clock)
    for second in range(0, 100, 5):
        clock.now = second
        assert not idle.step(has_client=False, heartbeat_fresh=True, last_activity=0)
    clock.now = 100
    assert not idle.step(has_client=False, heartbeat_fresh=False, last_activity=0)
    clock.now = 105
    assert idle.step(has_client=False, heartbeat_fresh=False, last_activity=0)


def test_resume_grants_sixty_seconds():
    clock = Clock()
    idle = IdleRetirement(timeout=10, tick=5, clock=clock)
    clock.now = 100
    assert not idle.step(has_client=False, heartbeat_fresh=False, last_activity=0)
    assert idle.grace_until == 160
    for second in (120, 140, 159):
        clock.now = second
        assert not idle.step(has_client=False, heartbeat_fresh=False, last_activity=0)
    clock.now = 160
    assert idle.step(has_client=False, heartbeat_fresh=False, last_activity=0)


def test_heartbeat_distinct_roots_and_error_keeps_alive(tmp_path, monkeypatch, caplog):
    app = SimpleNamespace(state=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(lifetime, "selected_workspace_root", lambda _state: tmp_path)
    reads = []
    monkeypatch.setattr(lifetime, "select_heartbeat", lambda root: (reads.append(root) or None, None))
    assert not lifetime.heartbeat_fresh(app)
    assert reads == [tmp_path.resolve()]
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(lifetime, "selected_workspace_root", lambda _state: workspace)
    reads.clear()

    def selected(root):
        reads.append(root)
        return ({"fresh": True}, "file") if root == workspace else (None, None)

    monkeypatch.setattr(lifetime, "select_heartbeat", selected)
    assert lifetime.heartbeat_fresh(app)
    assert set(reads) == {tmp_path, workspace} and len(reads) == 2

    def failed(_root):
        raise OSError("heartbeat unavailable")

    monkeypatch.setattr(lifetime, "select_heartbeat", failed)
    with caplog.at_level("INFO", logger="wg.launch"):
        assert lifetime.heartbeat_fresh(app)
        assert lifetime.heartbeat_fresh(app)
    assert caplog.text.count("Could not check WGLink heartbeat") == 1
    clock = Clock()
    idle = IdleRetirement(timeout=1, clock=clock)
    clock.now = 1
    assert not idle.step(has_client=False, heartbeat_fresh=lifetime.heartbeat_fresh(app), last_activity=0)


def test_release_waits_for_counts_and_three_seconds_quiet():
    release = ReleaseRequest({"requested_at": 100}, 0)
    assert release.step(now=1, has_client=True, last_start_wall=100, last_activity=1) == "wait"
    assert release.step(now=2, has_client=False, last_start_wall=100, last_activity=2) == "wait"
    assert release.step(now=4.9, has_client=False, last_start_wall=100, last_activity=2) == "wait"
    assert release.step(now=5, has_client=False, last_start_wall=100, last_activity=2) == "commit"


def test_new_start_cancels_but_old_client_end_does_not():
    release = ReleaseRequest({"requested_at": 100}, 0)
    assert release.step(now=4, has_client=False, last_start_wall=102.5, last_activity=0).startswith("cancel:")
    assert release.step(now=4, has_client=False, last_start_wall=99, last_activity=1) == "commit"


@pytest.mark.parametrize("start_offset", [1.5, 2.0])
def test_release_unload_margin_waits_for_client_end_and_quiet(start_offset):
    release = ReleaseRequest({"requested_at": 100}, 0)
    start = 100 + start_offset
    assert release.step(now=3, has_client=True, last_start_wall=start, last_activity=3) == "wait"
    assert release.step(now=4, has_client=False, last_start_wall=start, last_activity=4) == "wait"
    assert release.step(now=6.99, has_client=False, last_start_wall=start, last_activity=4) == "wait"
    assert release.step(now=7, has_client=False, last_start_wall=start, last_activity=4) == "commit"


def test_release_busy_at_ten_seconds_cancels():
    release = ReleaseRequest({"requested_at": 100}, 20)
    assert release.step(now=29.99, has_client=True, last_start_wall=99, last_activity=0) == "wait"
    assert "still" in release.step(now=30, has_client=True, last_start_wall=99, last_activity=0)


def _worker(tmp_path, *, clock=None):
    clock = Clock() if clock is None else clock
    hold = RestartApproval().retirement_hold
    app = SimpleNamespace(state=SimpleNamespace(retiring=False, retirement_hold=hold, jobs_runtime=None, data_dir=tmp_path))
    tracker = ActivityTracker(app, clock=clock)
    tracker.last_start_wall = 0
    stops = []
    worker = BackendLifetimeThread(tracker, stops.append, locks_dir=tmp_path, idle_timeout=3, started_at_wall=0, notify="poll")
    return worker, tracker, clock, stops


@pytest.mark.parametrize("scope", [{"type": "websocket"}, {"type": "http", "method": "POST", "path": "/api/solve"}])
def test_tracker_starts_change_start_times_ends_only_activity(scope, monkeypatch):
    async def scenario():
        clock = Clock()
        entered, finish = asyncio.Event(), asyncio.Event()

        async def app(*_args):
            entered.set()
            await finish.wait()

        monkeypatch.setattr(lifetime.time, "time", lambda: 100)
        tracker = ActivityTracker(app, clock=clock)
        clock.now = 2
        task = asyncio.create_task(tracker(scope, None, None))
        await entered.wait()
        assert (tracker.last_start, tracker.last_start_wall, tracker.last_activity) == (2, 100, 2)
        clock.now = 4
        finish.set()
        await task
        assert (tracker.last_start, tracker.last_start_wall, tracker.last_activity) == (2, 100, 4)

    asyncio.run(scenario())


@pytest.mark.parametrize("racing", ["none", "activity", "work", "websocket"])
def test_idle_commit_holds_rechecks_and_releases(tmp_path, monkeypatch, racing):
    async def scenario():
        worker, tracker, clock, stops = _worker(tmp_path)
        tracker.loop = asyncio.get_running_loop()
        hold = tracker.app.state.retirement_hold
        monkeypatch.setattr(lifetime, "heartbeat_fresh", lambda _app: False)
        clock.now = 3
        real_commit = tracker.retire_if_idle

        def commit(*args, **kwargs):
            assert hold.held
            if racing == "activity":
                tracker.last_activity = 3
            elif racing == "work":
                tracker.app.state.cad_delivery_in_flight = lambda: True
            elif racing == "websocket":
                tracker.websockets = 1
            return real_commit(*args, **kwargs)

        monkeypatch.setattr(tracker, "retire_if_idle", commit)
        await asyncio.to_thread(worker._step, idle_tick=True)
        assert bool(stops) is (racing == "none")
        assert hold.held is (racing == "none")
        worker.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("start_offset", [1.5, 2.0, 2.5])
def test_release_ignores_heartbeat_and_rechecks_new_start(tmp_path, monkeypatch, start_offset):
    async def scenario():
        worker, tracker, clock, stops = _worker(tmp_path)
        tracker.loop = asyncio.get_running_loop()
        monkeypatch.setattr(lifetime, "heartbeat_fresh", lambda _app: pytest.fail("release must not read heartbeat"))
        worker.release = ReleaseRequest({"requested_at": 100}, 0)
        clock.now = 3
        real_commit = tracker.retire_if_idle

        def raced(*args, **kwargs):
            assert tracker.app.state.retirement_hold.held
            tracker.last_start_wall = 100 + start_offset
            return real_commit(*args, **kwargs)

        monkeypatch.setattr(tracker, "retire_if_idle", raced)
        await asyncio.to_thread(worker._step, idle_tick=True)
        if start_offset <= lifetime.RELEASE_START_MARGIN:
            assert stops == [lifetime.RELEASE_REASON]
            assert tracker.app.state.retirement_hold.held
            assert worker.release is None
            worker.close()
            return
        assert not stops and not tracker.app.state.retirement_hold.held
        await asyncio.to_thread(worker._step, idle_tick=False)
        assert worker.release is None
        tracker.last_start_wall = 99
        worker.release = ReleaseRequest({"requested_at": 100}, 3)
        monkeypatch.setattr(tracker, "retire_if_idle", real_commit)
        await asyncio.to_thread(worker._step, idle_tick=False)
        assert stops == [lifetime.RELEASE_REASON]
        worker.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("expire", [False, True])
def test_retire_holds_immediately_activity_does_not_cancel_and_cap_releases(tmp_path, expire):
    async def scenario():
        worker, tracker, clock, stops = _worker(tmp_path)
        tracker.loop = asyncio.get_running_loop()
        tracker.websockets = 1
        files.write_request(tmp_path, pid=os.getpid(), mode="retire", reason="update", now=lambda: 100)
        await asyncio.to_thread(worker._take_request)
        hold = tracker.app.state.retirement_hold
        assert hold.held and "an update is replacing it" in hold.refusal()
        tracker.last_start_wall = 101
        clock.now = 130 if expire else 5
        tracker.websockets = int(expire)
        await asyncio.to_thread(worker._step, idle_tick=True)
        assert bool(stops) is (not expire)
        assert hold.held is (not expire)
        if expire:
            assert worker.update is None
        worker.close()

    asyncio.run(scenario())


def test_retire_replaces_release_and_ignores_other_active_requests(tmp_path):
    worker, tracker, _clock, _stops = _worker(tmp_path)
    tracker.has_client = lambda: True
    worker.release = ReleaseRequest({"requested_at": 0}, 0)
    files.write_request(tmp_path, pid=os.getpid(), mode="retire", reason="update")
    worker._take_request()
    assert worker.release is None and worker.update is not None
    update = worker.update
    for mode in ("release", "retire"):
        files.write_request(tmp_path, pid=os.getpid(), mode=mode, reason="second")
        worker._take_request()
        assert worker.update is update and not (tmp_path / files.REQUEST_FILENAME).exists()
    tracker.app.state.retirement_hold.release(owner="update")
    worker.close()


def test_multi_owner_hold_uses_latest_reason_and_only_last_release_wakes():
    approval = RestartApproval()
    wakes = []
    approval.add_release_listener(lambda *args: wakes.append(args))
    hold = approval.retirement_hold
    hold.hold("installer")
    hold.hold("update", owner="update")
    hold.hold("idle", owner="idle")
    assert "idle" in hold.refusal()
    hold.hold("new update", owner="update")
    assert "new update" in hold.refusal()
    hold.release(owner="update")
    assert hold.held and "idle" in hold.refusal() and not wakes
    hold.release(owner="idle")
    assert hold.held and "installer" in hold.refusal() and not wakes
    hold.release()
    assert not hold.held and hold.refusal() is None and len(wakes) == 1
    hold.release(owner="missing")
    assert len(wakes) == 1


@pytest.mark.parametrize("field", ["http_requests", "websockets", "cad_delivery", "jobs"])
def test_release_waits_for_real_tracker_clients_to_leave(tmp_path, field):
    async def scenario():
        worker, tracker, clock, stops = _worker(tmp_path)
        tracker.loop = asyncio.get_running_loop()
        tracker.last_start_wall = 101.5 if field in ("http_requests", "websockets") else 99
        worker.release = ReleaseRequest({"requested_at": 100}, 0)
        active = [True]
        if field in ("http_requests", "websockets"):
            setattr(tracker, field, 1)
        elif field == "cad_delivery":
            tracker.app.state.cad_delivery_in_flight = lambda: active[0]
        else:
            tracker.app.state.jobs_runtime = SimpleNamespace(running_job_ids=("job",))
        clock.now = 3
        await asyncio.to_thread(worker._step, idle_tick=False)
        assert not stops and worker.release is not None
        if field in ("http_requests", "websockets"):
            setattr(tracker, field, 0)
        elif field == "cad_delivery":
            active[0] = False
        else:
            tracker.app.state.jobs_runtime = None
        tracker.last_activity = 3
        clock.now = 5.9
        await asyncio.to_thread(worker._step, idle_tick=False)
        assert not stops
        clock.now = 6
        await asyncio.to_thread(worker._step, idle_tick=False)
        assert stops == [lifetime.RELEASE_REASON]
        worker.close()

    asyncio.run(scenario())


def test_update_hold_refuses_new_submission_routes_in_process(tmp_path):
    from server.app import create_app

    async def scenario():
        app = create_app(data_dir=tmp_path, solver_warmup=False)
        app.state.retirement_hold.hold(lifetime.UPDATE_REASON, owner="update")
        messages = []

        async def receive():
            return {"type": "http.request", "body": json.dumps({
                "design": {"formula": "OSSE", "L": 120, "a": 45},
                "options": {"engine": "retirement-test-missing"},
            }).encode(), "more_body": False}

        async def send(message):
            messages.append(message)

        try:
            for route in ("/api/solve", "/api/cadlink/ingest"):
                messages.clear()
                await app({
                    "type": "http", "method": "POST", "path": route, "raw_path": route.encode(),
                    "query_string": b"", "headers": [
                        (b"content-type", b"application/json"), (b"host", b"127.0.0.1:3100"),
                    ],
                    "scheme": "http", "server": ("127.0.0.1", 3100), "client": ("127.0.0.1", 1234),
                    "http_version": "1.1",
                }, receive, send)
                assert messages[0]["status"] == 409
                body = b"".join(message.get("body", b"") for message in messages[1:])
                error = json.loads(body)["error"]
                assert error["code"] == "update_restart_pending" and error["retryable"] is True
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("retiring", [True, False])
def test_monitor_close_releases_only_its_owners_unless_retiring(tmp_path, retiring):
    worker, tracker, _clock, _stops = _worker(tmp_path)
    hold = tracker.app.state.retirement_hold
    for owner in ("installer", "update", "gui-quit", "idle"):
        hold.hold(owner, owner=owner)
    tracker.app.state.retiring = retiring
    worker.stop.set()
    worker._run()
    worker.close()
    assert hold.held
    assert ("idle" in hold.refusal()) is retiring
    hold.release(owner="installer")
    assert hold.held is retiring


def test_no_serving_loop_fails_closed(tmp_path, monkeypatch):
    worker, tracker, clock, stops = _worker(tmp_path)
    monkeypatch.setattr(lifetime, "heartbeat_fresh", lambda _app: False)
    clock.now = 3
    worker._step(idle_tick=True)
    assert not stops and not tracker.app.state.retiring
    worker.close()


def test_failed_activity_snapshot_cancels_release_with_accurate_reason(tmp_path, caplog):
    worker, tracker, _clock, stops = _worker(tmp_path)
    assert tracker.loop is None
    worker.release = ReleaseRequest({"requested_at": 100}, 0)
    try:
        with caplog.at_level("INFO", logger="wg.launch"):
            worker._step(idle_tick=False)
        assert worker.release is None and not stops
        assert "Backend release cancelled (activity could not be checked)" in caplog.text
        assert "a new client started" not in caplog.text
    finally:
        worker.close()


@pytest.mark.parametrize("shutdown_started", [True, False])
def test_failed_stop_keeps_commit_hold_when_shutdown_started(tmp_path, shutdown_started):
    async def scenario():
        worker, tracker, _clock, stops = _worker(tmp_path)
        tracker.loop = asyncio.get_running_loop()
        hold = tracker.app.state.retirement_hold
        worker.stop_started = lambda: shutdown_started

        def failed_stop(reason):
            assert tracker.app.state.retiring and hold.held
            stops.append(reason)
            raise RuntimeError("could not start the shutdown budget")

        worker.request_stop = failed_stop
        try:
            assert not await asyncio.to_thread(worker._commit, lifetime.RELEASE_REASON, "gui-quit", lambda: True)
            assert stops == [lifetime.RELEASE_REASON]
            assert tracker.app.state.retiring is shutdown_started
            assert hold.held is shutdown_started
        finally:
            worker.close()

    asyncio.run(scenario())


def _payload(**changes):
    return {"schema": 1, "pid": 42, "mode": "release", "reason": "gui-quit", "requested_at": 100.0, **changes}


@pytest.mark.parametrize("changes", [
    {"pid": 43}, {"schema": 2}, {"schema": True}, {"mode": "other"}, {"reason": None},
    {"reason": "x" * 129}, {"requested_at": 97}, {"requested_at": float("nan")},
    {"requested_at": float("inf")}, {"requested_at": True}, {"requested_at": "100"},
])
def test_invalid_request_is_always_deleted(tmp_path, changes, caplog):
    path = tmp_path / files.REQUEST_FILENAME
    path.write_text(json.dumps(_payload(**changes)), encoding="utf-8")
    with caplog.at_level("INFO", logger="wg.launch"):
        assert files.take_request(tmp_path, pid=42, started_at_wall=100, now=lambda: 110) is None
        assert files.take_request(tmp_path, pid=42, started_at_wall=100) is None
    assert not path.exists()
    assert caplog.text.count("request ignored") == 1


@pytest.mark.parametrize("raw", [b"{", b"[]", b"\xff", b"x" * 4097], ids=["malformed", "array", "unicode", "oversize"])
def test_malformed_and_oversize_deleted_without_reading_oversize(tmp_path, monkeypatch, raw):
    path = tmp_path / files.REQUEST_FILENAME
    path.write_bytes(raw)
    if len(raw) > 4096:
        monkeypatch.setattr(files.os, "open", lambda *_args: pytest.fail("oversize must be deleted unread"))
        monkeypatch.setattr(files, "_open_request", lambda *_args: pytest.fail("oversize must be deleted unread"))
    assert files.take_request(tmp_path, pid=42, started_at_wall=0) is None
    assert not path.exists()


def test_symlink_is_unlinked_never_read(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text(json.dumps(_payload()), encoding="utf-8")
    link = tmp_path / files.REQUEST_FILENAME
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable to this user")
    monkeypatch.setattr(files.os, "open", lambda *_args: pytest.fail("symlink must never be opened"))
    monkeypatch.setattr(files, "_open_request", lambda *_args: pytest.fail("symlink must never be opened"))
    assert files.take_request(tmp_path, pid=42, started_at_wall=0) is None
    assert not link.is_symlink() and target.exists()


def test_delete_sharing_violation_retries_without_acting(tmp_path, monkeypatch):
    path = tmp_path / files.REQUEST_FILENAME
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    real_unlink = Path.unlink

    def blocked(self, *args, **kwargs):
        if self == path:
            raise PermissionError("sharing violation")
        return real_unlink(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", blocked)
        assert files.take_request(tmp_path, pid=42, started_at_wall=100) is None
        assert path.exists()
    assert files.take_request(tmp_path, pid=42, started_at_wall=100)["pid"] == 42
    assert not path.exists()


@pytest.mark.parametrize("notification_enabled", [False, True], ids=["poll", "notification"])
def test_monitor_retries_undeletable_request_within_one_second(tmp_path, monkeypatch, notification_enabled):
    worker, tracker, clock, stops = _worker(tmp_path)
    worker.tick = 60
    worker.poll_interval = 600
    worker.notify = "auto" if notification_enabled else "poll"
    worker.stop.close()

    class Stop(threading.Event):
        win32_handle = 10

        def close(self):
            pass

    worker.stop = Stop()
    notification_closed = []
    notification = SimpleNamespace(close=lambda: notification_closed.append(True))
    monkeypatch.setattr(lifetime, "os", SimpleNamespace(name="nt", getpid=os.getpid, path=os.path))
    monkeypatch.setattr(lifetime, "_RequestNotification", lambda *_args: notification)
    monkeypatch.setattr(tracker, "snapshot", lambda *_args: (True, 0, 0))
    monkeypatch.setattr(lifetime, "heartbeat_fresh", lambda _app: False)
    files.write_request(tmp_path, pid=os.getpid(), mode="release", reason="gui-quit")
    path = tmp_path / files.REQUEST_FILENAME
    real_unlink = Path.unlink
    waits, attempts = [], []
    blocked = True

    def unlink(self, *args, **kwargs):
        if self == path:
            attempts.append(clock.now)
            if blocked:
                raise PermissionError("sharing violation")
        return real_unlink(self, *args, **kwargs)

    def wait(timeout, observed_notification):
        nonlocal blocked
        assert observed_notification is (notification if notification_enabled else None)
        waits.append(timeout)
        clock.now += timeout
        if len(waits) == 1:
            assert path.exists() and worker.release is None and not stops
            blocked = False  # Closing the other handle sends no notification.
        else:
            assert worker.release is not None and not path.exists()
            worker.stop.set()

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(worker, "_wait", wait)
    try:
        worker._run()
        assert waits == [1.0, 0.25]
        assert attempts == [0.0, 1.0]
        assert notification_closed == ([True] if notification_enabled else [])
    finally:
        worker.close()


def test_request_future_clamped_and_two_second_start_tolerance(tmp_path):
    for stamp, expected in ((200, 110), (98, 98)):
        files.write_request(tmp_path, pid=42, mode="release", reason="gui-quit", now=lambda: stamp)
        request = files.take_request(tmp_path, pid=42, started_at_wall=100, now=lambda: 110)
        assert request == _payload(requested_at=expected)
        assert not (tmp_path / files.REQUEST_FILENAME).exists()


def test_atomic_write_fsyncs_then_replaces_in_same_directory(tmp_path, monkeypatch):
    directory = tmp_path / "locks"
    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(descriptor):
        events.append("fsync")
        real_fsync(descriptor)

    def replace(source, destination):
        assert source.parent == destination.parent == directory
        assert json.loads(source.read_text(encoding="utf-8")) == _payload()
        assert not destination.exists()
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(files.os, "fsync", fsync)
    monkeypatch.setattr(files.os, "replace", replace)
    files.write_request(directory, pid=42, mode="release", reason="gui-quit", now=lambda: 100)
    assert events == ["fsync", "replace"]
    assert list(directory.iterdir()) == [directory / files.REQUEST_FILENAME]


def test_atomic_write_error_is_raised_and_temporary_removed(tmp_path, monkeypatch):
    def failed(*_args):
        raise OSError("fsync failed")

    monkeypatch.setattr(files.os, "fsync", failed)
    with pytest.raises(OSError, match="fsync failed"):
        files.write_request(tmp_path, pid=42, mode="release", reason="gui-quit")
    assert not list(tmp_path.iterdir())


def test_poll_thread_takes_request_file(tmp_path, monkeypatch):
    worker, tracker, _clock, _stops = _worker(tmp_path, clock=time.monotonic)
    worker.poll_interval = 0.02
    taken = threading.Event()
    monkeypatch.setattr(tracker, "snapshot", lambda *_args: (True, 0, tracker.last_activity))
    monkeypatch.setattr(lifetime, "heartbeat_fresh", lambda _app: False)
    real_take = worker._take_request

    def take():
        real_take()
        if worker.release is not None:
            taken.set()

    monkeypatch.setattr(worker, "_take_request", take)
    worker.start()
    try:
        files.write_request(tmp_path, pid=os.getpid(), mode="release", reason="gui-quit")
        assert taken.wait(1)
        assert not (tmp_path / files.REQUEST_FILENAME).exists()
    finally:
        worker.close()
    assert not worker.thread.is_alive()


def test_inactive_thread_sleeps_full_ticks(tmp_path, monkeypatch):
    worker, tracker, clock, _stops = _worker(tmp_path)
    worker.poll_interval = 600
    waits = []
    monkeypatch.setattr(tracker, "snapshot", lambda *_args: (False, 0, 0))
    monkeypatch.setattr(lifetime, "heartbeat_fresh", lambda _app: False)

    def wait(timeout, notification):
        assert notification is None
        waits.append(timeout)
        clock.now += timeout
        if len(waits) == 3:
            worker.stop.set()

    monkeypatch.setattr(worker, "_wait", wait)
    worker._run()
    worker.close()
    assert waits == [worker.tick] * 3


def test_inactive_file_poll_does_not_scan_clients_between_idle_ticks(tmp_path, monkeypatch):
    worker, tracker, _clock, _stops = _worker(tmp_path)
    monkeypatch.setattr(tracker, "snapshot", lambda *_args: pytest.fail("only idle ticks need to scan clients"))
    worker._step(idle_tick=False)
    worker.close()


@pytest.mark.parametrize("mode", ["release", "retire"])
def test_active_request_still_updates_idle_presence_at_each_tick(tmp_path, mode, monkeypatch):
    worker, tracker, clock, _stops = _worker(tmp_path)
    monkeypatch.setattr(tracker, "snapshot", lambda *_args: (True, 0, 0))
    monkeypatch.setattr(tracker, "has_client", lambda: True)
    files.write_request(tmp_path, pid=os.getpid(), mode=mode, reason="test")
    worker._take_request()
    clock.now = 1
    worker._step(idle_tick=True)
    assert worker.idle.last_present == 1 and worker.idle.previous_step == 1
    tracker.app.state.retirement_hold.release(owner="update")
    worker.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows notifications")
def test_windows_notification_takes_file_with_long_poll_interval(tmp_path, monkeypatch):
    worker, tracker, _clock, _stops = _worker(tmp_path, clock=time.monotonic)
    worker.notify = "auto"
    worker.poll_interval = 600
    worker.tick = 60
    taken = threading.Event()
    armed = threading.Event()
    monkeypatch.setattr(tracker, "snapshot", lambda *_args: (True, 0, tracker.last_activity))
    real_wait, real_take = worker._wait, worker._take_request

    def wait(timeout, notification):
        assert notification is not None
        armed.set()
        real_wait(timeout, notification)

    def take():
        real_take()
        if worker.release is not None:
            taken.set()

    monkeypatch.setattr(worker, "_wait", wait)
    monkeypatch.setattr(worker, "_take_request", take)
    worker.start()
    try:
        assert armed.wait(1)
        files.write_request(tmp_path, pid=os.getpid(), mode="release", reason="gui-quit")
        assert taken.wait(1)
    finally:
        worker.close()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "bad"])
def test_idle_timeout_argument_is_positive_finite_and_hidden(value):
    from launch.serve import build_parser

    parser = build_parser()
    assert "--idle-timeout" not in parser.format_help()
    assert parser.parse_args([]).idle_timeout == 1800
    with pytest.raises(SystemExit):
        parser.parse_args(["--idle-timeout", value])


@pytest.mark.skipif(os.name != "nt", reason="Windows wait API")
def test_notification_flags_rearm_close_and_timeout_rounding(tmp_path, monkeypatch):
    import ctypes

    calls = []

    def first(path, subtree, flags):
        assert path == str(tmp_path) and subtree is False and flags == 0x19
        return 33

    def wait(count, handles, all_handles, milliseconds):
        assert count == 2 and list(handles) == [10, 33] and all_handles is False
        calls.append(milliseconds)
        return 1

    kernel = SimpleNamespace(
        FindFirstChangeNotificationW=first, WaitForMultipleObjects=wait,
        FindNextChangeNotification=lambda handle: calls.append(("rearm", handle)) or True,
        FindCloseChangeNotification=lambda handle: calls.append(("close", handle)) or True,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    notification = lifetime._RequestNotification(tmp_path, SimpleNamespace(win32_handle=10))
    notification.wait(1.0001)
    notification.close()
    notification.close()
    assert calls == [1001, ("rearm", 33), ("close", 33)]
