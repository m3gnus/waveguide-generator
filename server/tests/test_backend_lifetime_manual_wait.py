"""A manual-waiting CAD solve holds an idle or quitting backend for one hour only."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import os
from types import SimpleNamespace

import pytest

from launch import backend_lifetime as lifetime
from launch.backend_lifetime import (
    MANUAL_WAIT_CAP, ActivityTracker, BackendLifetimeThread, ManualWaitCap, ReleaseRequest, RetirementWatcher,
)
from launchers import backend_retire_request as files
from server.jobs.store import JobStore
from server.updates.restart import RestartApproval

from cad_backends import JobsHarness
from test_cad_manual_solve import _create, _ingest

CREATED = datetime(2026, 10, 12, 12, 0, 0)
MINUTE = 60.0


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _row(job_id, *, status="preparing", manual=True, claimed=False, created=CREATED):
    stamp = created if isinstance(created, str) else created.isoformat()
    return {
        "id": job_id, "status": status, "created_at": stamp, "updated_at": stamp, "queued_at": stamp,
        "started_at": stamp if claimed else None, "config_json": {}, "config_summary_json": {},
        "task_metadata": {"cad": {"operation_id": job_id, "manual_waiting": True}} if manual else {},
    }


def _store(tmp_path, *rows):
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    for row in rows:
        store.create_job(row)
    return store


def _tracker(store, clock):
    runtime = SimpleNamespace(store=store, running_job_ids=())
    hold = RestartApproval().retirement_hold
    app = SimpleNamespace(state=SimpleNamespace(
        retiring=False, retirement_hold=hold, jobs_runtime=runtime, data_dir=None,
    ))
    return ActivityTracker(app, clock=clock)


def _wall(clock, start=CREATED):
    """The wall clock moves with the suspend-inclusive clock, as both include suspend."""
    return lambda: start + timedelta(seconds=clock.now)


def _worker(tmp_path, store, clock, *, idle_timeout=1800, start=CREATED):
    tracker = _tracker(store, clock)
    tracker.last_start_wall = 0
    stops = []
    worker = BackendLifetimeThread(
        tracker, stops.append, locks_dir=tmp_path, idle_timeout=idle_timeout, started_at_wall=0, notify="poll",
    )
    worker.manual_wait.wall = _wall(clock, start)
    return worker, tracker, stops


def test_the_production_clock_and_one_hour_cap():
    from server.solver.beat_runtime.clock import suspend_aware_monotonic

    assert MANUAL_WAIT_CAP == 3600
    tracker = ActivityTracker(SimpleNamespace(state=SimpleNamespace()), clock=suspend_aware_monotonic)
    worker = BackendLifetimeThread(tracker, lambda _reason: None, locks_dir=".", started_at_wall=0)
    try:
        # The cap reads the same clock object as the idle timer.
        assert worker.manual_wait.clock is worker.idle.clock is suspend_aware_monotonic
    finally:
        worker.stop.close()


@pytest.mark.parametrize("minute, capped", [(0, True), (59, True), (59.99, True), (60, False), (61, False)])
def test_a_real_manual_solve_holds_for_59_minutes_and_not_61(tmp_path, minute, capped):
    harness = JobsHarness(tmp_path)
    try:
        ingest_id, _copy = _ingest(harness)
        assert _create(harness, ingest_id).operation.state == "received"
        job = harness.jobs_store.latest_cad_job("manual-1")
        assert job["status"] == "preparing" and job["started_at"] is None
        assert job["task_metadata"]["cad"]["manual_waiting"] is True
        clock = Clock()
        created = datetime.fromisoformat(job["created_at"])
        cap = ManualWaitCap(clock, wall=_wall(clock, created))
        tracker = ActivityTracker(SimpleNamespace(state=SimpleNamespace(jobs_runtime=harness.runtime)), clock=clock)
        assert asyncio.run(tracker.holds_work(cap))  # First sight, as it was created.
        clock.now = minute * MINUTE
        assert asyncio.run(tracker.holds_work(cap)) is capped
        # Every other user of the predicate still counts it.
        assert asyncio.run(tracker.holds_work()) is True
    finally:
        harness.close()


@pytest.mark.parametrize("other, held", [
    (None, False), ("queued", True), ("running", True), ("preparing", True), ("claimed", True),
])
def test_only_an_expired_manual_wait_stops_holding(tmp_path, other, held):
    rows = [_row("manual")]
    if other is not None:
        rows.append(_row(
            "other", status="preparing" if other == "claimed" else other, manual=False,
            claimed=other in ("running", "claimed"),
        ))
    store = _store(tmp_path, *rows)
    try:
        clock = Clock()
        cap = ManualWaitCap(clock, wall=_wall(clock))
        tracker = _tracker(store, clock)
        assert asyncio.run(tracker.holds_work(cap))
        clock.now = 61 * MINUTE
        assert asyncio.run(tracker.holds_work(cap)) is held
        # Queued or running work answers first; a later first sight still
        # counts the wait from its creation time.
        assert set(cap.seen) == (set() if other in ("queued", "running") else {"manual"})
    finally:
        store.close()


def test_runtime_lanes_and_delivery_still_hold_with_the_cap(tmp_path):
    store = _store(tmp_path, _row("manual"))
    try:
        clock = Clock()
        clock.now = 61 * MINUTE
        cap = ManualWaitCap(clock, wall=_wall(clock))
        tracker = _tracker(store, clock)
        assert not asyncio.run(tracker.holds_work(cap))
        tracker.app.state.jobs_runtime._prep_running = ("other",)
        assert asyncio.run(tracker.holds_work(cap))
        tracker.app.state.jobs_runtime._prep_running = ()
        tracker.app.state.cad_delivery_in_flight = lambda: True
        assert asyncio.run(tracker.holds_work(cap))
    finally:
        store.close()


@pytest.mark.parametrize("created, waited", [
    ("not a time", 0.0), ((CREATED + timedelta(hours=2)).isoformat(), 0.0),
    ((CREATED - timedelta(minutes=10)).isoformat(), 600.0),
])
def test_doubtful_creation_times_make_the_wait_newer_not_older(created, waited):
    clock = Clock()
    cap = ManualWaitCap(clock, wall=lambda: CREATED)
    assert cap.holds([_row("manual", created=created)])
    assert cap.seen["manual"] == (0.0, waited)
    clock.now = MANUAL_WAIT_CAP - waited - 1
    assert cap.holds([_row("manual", created=created)])
    clock.now = MANUAL_WAIT_CAP - waited
    assert not cap.holds([_row("manual", created=created)])


def test_more_preparing_rows_than_one_read_fail_closed(tmp_path, monkeypatch):
    store = _store(tmp_path, _row("a"), _row("b"))
    try:
        clock = Clock()
        clock.now = 2 * MANUAL_WAIT_CAP
        cap = ManualWaitCap(clock, wall=_wall(clock))
        tracker = _tracker(store, clock)
        assert not asyncio.run(tracker.holds_work(cap))
        monkeypatch.setattr(lifetime, "MANUAL_WAIT_SCAN_LIMIT", 1)
        assert asyncio.run(tracker.holds_work(cap))
    finally:
        store.close()


def test_idle_exit_waits_one_hour_then_the_idle_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(lifetime, "heartbeat_fresh", lambda _app: False)
    store = _store(tmp_path, _row("manual"))

    async def scenario():
        clock = Clock()
        worker, tracker, stops = _worker(tmp_path, store, clock)
        tracker.loop = asyncio.get_running_loop()
        try:
            for second in range(0, int(59 * MINUTE + worker.idle.timeout), 60):
                clock.now = second
                await asyncio.to_thread(worker._step, idle_tick=True)
                # Held through 59 minutes, so the idle count starts from there.
                assert not stops, second
                assert worker.idle.last_present == min(second, 59 * MINUTE)
            clock.now = 59 * MINUTE + worker.idle.timeout
            await asyncio.to_thread(worker._step, idle_tick=True)
            assert stops == [worker.idle.reason]
        finally:
            worker.close()

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_suspend_counts_as_on_the_idle_clock_and_resume_still_gets_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(lifetime, "heartbeat_fresh", lambda _app: False)
    store = _store(tmp_path, _row("manual"))

    async def scenario():
        clock = Clock()
        worker, tracker, stops = _worker(tmp_path, store, clock)
        tracker.loop = asyncio.get_running_loop()
        try:
            for second in range(0, 1260, 60):
                clock.now = second
                await asyncio.to_thread(worker._step, idle_tick=True)
            assert worker.idle.last_present == 1200
            # Two hours asleep: the idle clock includes them, so the wait has
            # expired, but a resume never ends the backend on its first tick.
            clock.now = 1200 + 7200
            await asyncio.to_thread(worker._step, idle_tick=True)
            assert not stops and worker.idle.grace_until == clock.now + 60
            assert not tracker.app.state.retirement_hold.held
            clock.now += 60
            await asyncio.to_thread(worker._step, idle_tick=True)
            assert stops == [worker.idle.reason]
        finally:
            worker.close()

    try:
        asyncio.run(scenario())
    finally:
        store.close()


@pytest.mark.parametrize("minute, released", [(59, False), (61, True)])
def test_gui_quit_release_ignores_a_manual_wait_after_one_hour(tmp_path, minute, released):
    store = _store(tmp_path, _row("manual"))

    async def scenario():
        clock = Clock()
        worker, tracker, stops = _worker(tmp_path, store, clock)
        tracker.loop = asyncio.get_running_loop()
        try:
            tracker.last_start_wall = 99
            clock.now = minute * MINUTE
            tracker.last_activity = clock.now  # The quitting window's last request.
            worker.release = ReleaseRequest({"requested_at": 100}, clock.now)
            await asyncio.to_thread(worker._step, idle_tick=False)
            assert not stops
            clock.now += 10
            await asyncio.to_thread(worker._step, idle_tick=False)
            if released:
                assert stops == [lifetime.RELEASE_REASON] and worker.release is None
                assert tracker.app.state.retirement_hold.held
            else:
                assert not stops and worker.release is None  # Cancelled: it still holds work.
                assert not tracker.app.state.retirement_hold.held
        finally:
            worker.close()

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_installer_hold_still_counts_an_expired_manual_wait(tmp_path):
    store = _store(tmp_path, _row("manual"))

    async def scenario():
        clock = Clock()
        worker, tracker, stops = _worker(tmp_path, store, clock)
        tracker.loop = asyncio.get_running_loop()
        try:
            clock.now = 2 * MANUAL_WAIT_CAP
            client, _start, _activity = await asyncio.to_thread(tracker.snapshot, worker.manual_wait)
            assert not client  # Expired for the idle exit and the Quit release...
            assert await asyncio.to_thread(tracker.has_client)  # ...but not for the installer.
            hold = tracker.app.state.retirement_hold
            # The InstallerRetireThread wiring: the uncapped predicate and commit.
            watcher = RetirementWatcher(
                hold, tracker.has_client,
                lambda: tracker.retire_if_idle(stops.append, lifetime.RETIRE_REASON),
                clock=clock,
            )
            await asyncio.to_thread(watcher.step, True)
            assert hold.held and not stops and not watcher.retired
            clock.now += 129
            await asyncio.to_thread(watcher.step, True)
            assert hold.held and not stops
            clock.now += 1
            await asyncio.to_thread(watcher.step, True)
            assert not hold.held and not stops  # The 130 s cap is unchanged.
        finally:
            worker.close()

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_update_retire_request_still_counts_an_expired_manual_wait(tmp_path):
    store = _store(tmp_path, _row("manual"))

    async def scenario():
        clock = Clock()
        clock.now = 2 * MANUAL_WAIT_CAP
        worker, tracker, stops = _worker(tmp_path, store, clock)
        tracker.loop = asyncio.get_running_loop()
        try:
            files.write_request(tmp_path, pid=os.getpid(), mode="retire", reason="update", now=lambda: 100)
            await asyncio.to_thread(worker._take_request)
            hold = tracker.app.state.retirement_hold
            assert hold.held and not stops
            clock.now += 129
            await asyncio.to_thread(worker._step, idle_tick=False)
            assert hold.held and not stops
            clock.now += 1
            await asyncio.to_thread(worker._step, idle_tick=False)
            assert not hold.held and not stops and worker.update is None
        finally:
            worker.close()

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_a_manual_wait_survives_restart_and_keeps_its_age(tmp_path):
    store = _store(tmp_path, _row("manual"), _row("claimed", manual=False, claimed=True))
    store.close()
    # The next backend: the same database, with startup recovery.
    store = JobStore(tmp_path / "jobs.db")
    try:
        store.initialize()
        store.recover_on_startup("restarted")
        row = store.get_job_row("manual")
        assert row["status"] == "preparing" and row["started_at"] is None
        assert row["task_metadata"]["cad"]["manual_waiting"] is True
        assert "manual" not in store.unheld_preparing_job_ids()  # It waits for a press.
        assert store.get_job_row("claimed")["status"] == "error"  # A lane's claim did not survive.
        rows, _total = store.list_jobs(statuses=("preparing",))
        assert [item["id"] for item in rows] == ["manual"]
        # It was created 30 minutes before this backend first saw it.
        clock = Clock()
        cap = ManualWaitCap(clock, wall=_wall(clock, CREATED + timedelta(minutes=30)))
        tracker = _tracker(store, clock)
        assert asyncio.run(tracker.holds_work(cap))
        clock.now = 29 * MINUTE
        assert asyncio.run(tracker.holds_work(cap))
        clock.now = 31 * MINUTE
        assert not asyncio.run(tracker.holds_work(cap))
        assert asyncio.run(tracker.holds_work())
    finally:
        store.close()
