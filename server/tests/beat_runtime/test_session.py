from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from server.platform import temp_session
from server.solver.beat_runtime.manager import ManagedWorker
from server.solver.beat_runtime.session import SolveSession


@pytest.fixture(autouse=True)
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))
    return tmp_path


class Stream:
    def __init__(self, worker, request):
        self.worker, self.request = worker, request
        self.count = 0
        self.closed = False

    def __next__(self):
        if self.closed:
            raise StopIteration
        if self.count == 0:
            self.count += 1
            return {"type": "result", "value": 1}
        if self.request.get("block"):
            self.worker.reading.set()
            assert self.worker.terminated.wait(2)
            raise RuntimeError("interrupted read")
        cancelled = Path(self.request["cancel_path"]).exists()
        return {"type": "cancelled" if cancelled else "completed", "solved_count": 1}

    def close(self):
        self.closed = True
        if self.request.get("close_error"):
            raise OSError("stream closure failed")


class Worker:
    worker_info = {"ready": True}

    def __init__(self):
        self.terminated = threading.Event()
        self.reading = threading.Event()
        self.retirements = 0
        self.submissions = 0

    def ensure_started(self):
        pass

    def submit(self, path, **kwargs):
        self.submissions += 1
        return Stream(self, json.loads(path.read_text()))

    def terminate(self):
        self.retirements += 1
        self.terminated.set()


def test_cooperative_cancel_keeps_partial_results_and_warm_worker(staging):
    worker = Worker()
    client = ManagedWorker(worker, "child")
    with SolveSession() as session:
        session.submit(client, {})
        events = session.events()
        assert next(events)["type"] == "result"
        session.request_cancel()
        assert next(events)["type"] == "cancelled"
    assert worker.retirements == 0
    assert list(staging.iterdir()) == []


def test_backstop_interrupts_blocked_read_and_preserves_partial_results(staging):
    worker = Worker()
    cancel = threading.Event()

    def callback():
        if cancel.is_set():
            raise RuntimeError("job cancelled")

    with SolveSession(cancellation_callback=callback, cancel_grace_s=0) as session:
        session.submit(ManagedWorker(worker, "child"), {"block": True})
        events = session.events()
        assert next(events)["type"] == "result"
        cancel.set()
        assert next(events) == {"type": "cancelled", "solved_count": 1}
    assert worker.retirements == 1
    assert list(staging.iterdir()) == []


def test_abandon_before_first_read_closes_and_stale_owner_cannot_cancel_successor(staging):
    worker = Worker()
    client = ManagedWorker(worker, "child")
    with SolveSession() as first:
        first.submit(client, {})
        old_lease = first._lease
    assert worker.retirements == 1
    with SolveSession() as second:
        second.submit(client, {})
        old_lease.cancel()
        old_lease.close()
        first.request_cancel()
        first.close()
        assert list(second.events())[-1]["type"] == "completed"
    assert worker.retirements == 1
    assert list(staging.iterdir()) == []


def test_bad_serialization_and_negotiation_leave_warm_worker_untouched(staging):
    worker = Worker()
    client = ManagedWorker(worker, "child")
    with pytest.raises(TypeError) as failure:
        with SolveSession() as session:
            session.submit(client, {"invalid": object()})
    assert failure.traceback and list(staging.iterdir()) == []

    def refuse(*args):
        raise ValueError("incompatible contract")

    with pytest.raises(ValueError, match="incompatible contract"):
        with SolveSession() as session:
            session.submit(client, {}, negotiate=refuse)
    assert worker.submissions == worker.retirements == 0
    assert list(staging.iterdir()) == []
    with SolveSession() as session:
        session.submit(client, {})
        assert list(session.events())[-1]["type"] == "completed"


def test_close_failure_unwinds_staging_and_condemns_client(staging):
    worker = Worker()
    client = ManagedWorker(worker, "child")
    with pytest.raises(OSError, match="stream closure failed"):
        with SolveSession() as session:
            session.submit(client, {"close_error": True})
    assert client.unusable
    assert list(staging.iterdir()) == []


def test_waiting_session_cancel_does_not_retire_current_owner(staging):
    worker = Worker()
    client = ManagedWorker(worker, "child")
    error = []
    waiting = threading.Event()
    cancel = threading.Event()

    def check():
        waiting.set()
        if cancel.is_set():
            raise RuntimeError("queued job cancelled")

    def run():
        try:
            with SolveSession(cancellation_callback=check) as session:
                session.submit(client, {})
        except RuntimeError as exc:
            error.append(str(exc))

    with SolveSession() as owner:
        owner.submit(client, {})
        thread = threading.Thread(target=run)
        thread.start()
        assert waiting.wait(1)
        cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert list(owner.events())[-1]["type"] == "completed"
    assert error == ["queued job cancelled"]
    assert worker.retirements == 0
    assert list(staging.iterdir()) == []


def test_cancel_during_startup_retires_a_late_start_and_submits_nothing(staging):
    cancel = threading.Event()

    class LateWorker(Worker):
        def ensure_started(self):
            cancel.set()
            assert self.terminated.wait(2)
            # Model ensure_started entering just after the first termination.
            self.alive = True

        def terminate(self):
            super().terminate()
            self.alive = False

    def callback():
        if cancel.is_set():
            raise RuntimeError("startup cancelled")

    worker = LateWorker()
    with pytest.raises(RuntimeError, match="startup cancelled"):
        with SolveSession(cancellation_callback=callback, cancel_grace_s=0) as session:
            session.submit(ManagedWorker(worker, "child"), {})
    assert not worker.alive and worker.submissions == 0
    assert worker.retirements == 2
    assert list(staging.iterdir()) == []


def test_submit_failure_preserves_original_error_and_releases_lease(staging):
    class BrokenWorker(Worker):
        def submit(self, path, **kwargs):
            raise ValueError("submission failed")

    client = ManagedWorker(BrokenWorker(), "child")
    with pytest.raises(ValueError, match="submission failed"):
        with SolveSession() as session:
            session.submit(client, {})
    assert client._lease is None
    assert list(staging.iterdir()) == []


def test_cancel_marker_failure_still_runs_retirement_backstop(staging, monkeypatch):
    worker = Worker()
    with SolveSession(cancel_grace_s=0) as session:
        session.submit(ManagedWorker(worker, "child"), {"block": True})
        events = session.events()
        assert next(events)["type"] == "result"
        original = Path.touch

        def touch(path, *args, **kwargs):
            if path == session.cancel_path:
                raise OSError("marker unavailable")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "touch", touch)
        session.request_cancel()
        assert next(events)["type"] == "cancelled"
    assert worker.retirements == 1
    assert list(staging.iterdir()) == []


def test_child_quit_cancels_active_work_and_wakes_waiters(staging):
    from server.solver.beat_runtime.ownership import OwnershipClosed

    worker = Worker()
    client = ManagedWorker(worker, "child")
    parked = threading.Event()
    closed = threading.Event()

    def wait():
        try:
            client.acquire(parked.set)
        except OwnershipClosed:
            closed.set()

    with SolveSession() as session:
        session.submit(client, {})
        waiter = threading.Thread(target=wait)
        waiter.start()
        assert parked.wait(1)
        client.close(detach=True)
        waiter.join(2)
        assert not waiter.is_alive() and closed.is_set()
    assert worker.terminated.is_set()
    assert list(staging.iterdir()) == []


def test_read_failure_is_not_masked_by_retirement_failure(staging):
    class BrokenStream:
        def __next__(self):
            raise ValueError("original reader failure")

        def close(self):
            raise OSError("secondary closure failure")

    class BrokenWorker(Worker):
        def submit(self, path, **kwargs):
            return BrokenStream()

        def terminate(self):
            raise RuntimeError("secondary retirement failure")

    client = ManagedWorker(BrokenWorker(), "child")
    with pytest.raises(ValueError, match="original reader failure"):
        with SolveSession() as session:
            session.submit(client, {})
            list(session.events())
    assert client.unusable and client._lease is None
    assert list(staging.iterdir()) == []
