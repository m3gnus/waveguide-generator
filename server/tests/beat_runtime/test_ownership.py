from __future__ import annotations

import gc
import threading

import pytest

from server.solver.beat_runtime.ownership import OwnershipClosed, StreamOwnership

GUARD = 5.0


class FakeStream:
    def __init__(self, events=(), *, on_close=None, on_read=None):
        self.events = iter(events)
        self.on_close = on_close
        self.on_read = on_read
        self.closes = 0

    def __next__(self):
        if self.on_read:
            self.on_read()
        return next(self.events)

    def close(self):
        self.closes += 1
        if self.on_close:
            self.on_close()


class FakeWorker:
    def __init__(self, streams):
        self.streams = iter(streams)
        self.calls = []

    def submit(self, request, *, status_callback=None, operation="solve"):
        self.calls.append((request, operation))
        if status_callback:
            status_callback("submitting")
        return next(self.streams)


def launch(action):
    outcome = {}

    def run():
        try:
            outcome["value"] = action()
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def join(thread):
    thread.join(GUARD)
    assert not thread.is_alive(), "ownership operation hung"


def fail():
    raise RuntimeError("callback broke")


def test_close_before_first_read_and_stale_cancellation():
    first, second = FakeStream(), FakeStream([{"type": "result"}])
    worker = FakeWorker([first, second])
    owner = StreamOwnership(worker)
    assert not owner.holds(None) and not owner.cancel(None)
    earlier = owner.submit({"first": 1})
    earlier.close()
    later = owner.submit({"second": 2}, operation="bem_field")
    earlier.close()
    assert not earlier.cancel() and not owner.cancel(earlier.token) and not owner.cancel(object())
    assert owner.holds(later.token) and second.closes == 0 and first.closes == 1
    assert next(later) == {"type": "result"}
    assert worker.calls == [({"first": 1}, "solve"), ({"second": 2}, "bem_field")]
    later.close()


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed"])
def test_terminal_releases_without_extra_read(terminal):
    raw = FakeStream([{"type": terminal}])
    owner = StreamOwnership(FakeWorker([raw, FakeStream()]))
    stream = owner.submit({})
    assert next(stream) == {"type": terminal}
    assert not owner.holds(stream.token) and raw.closes == 1
    successor = owner.submit({})
    stream.close()
    assert owner.holds(successor.token)
    with pytest.raises(StopIteration):
        next(stream)
    successor.close()


@pytest.mark.parametrize("terminal", [False, True])
def test_callback_error_releases_stream(terminal):
    raw = FakeStream([{"type": "completed" if terminal else "result"}])
    owner = StreamOwnership(FakeWorker([raw, FakeStream()]))
    stream = owner.submit({}, event_callback=lambda event: fail())
    with pytest.raises(RuntimeError, match="callback broke"):
        next(stream)
    assert raw.closes == 1 and not owner.holds(stream.token)
    owner.submit({}).close()


def test_status_callback_failure_during_locked_startup_retires_after_submit_returns():
    engine_lock = threading.Lock()
    closed = []

    def close():
        with engine_lock:
            closed.append(True)

    raw = FakeStream(on_close=close)

    class LockedWorker(FakeWorker):
        def submit(self, request, **kwargs):
            with engine_lock:
                return super().submit(request, **kwargs)

    owner = StreamOwnership(LockedWorker([raw, FakeStream()]))
    starter, started = launch(lambda: owner.submit({}, status_callback=lambda text: fail()))
    join(starter)
    assert isinstance(started.get("error"), RuntimeError)
    assert str(started["error"]) == "callback broke"
    assert raw.closes == 1 and closed == [True]
    owner.submit({}).close()


def test_terminal_callback_failure_cannot_cancel_successor():
    successor = []
    raw = FakeStream()
    owner = StreamOwnership(FakeWorker([FakeStream([{"type": "completed"}]), raw]))

    def callback(event):
        successor.append(owner.submit({}))
        fail()

    earlier = owner.submit({}, event_callback=callback)
    with pytest.raises(RuntimeError, match="callback broke"):
        next(earlier)
    assert owner.holds(successor[0].token) and raw.closes == 0
    successor[0].close()


@pytest.mark.parametrize("on_read", [None, fail])
def test_read_failure_or_exhaustion_releases_client(on_read):
    raw = FakeStream(on_read=on_read)
    owner = StreamOwnership(FakeWorker([raw, FakeStream()]))
    stream = owner.submit({})
    with pytest.raises(RuntimeError if on_read else StopIteration):
        next(stream)
    assert raw.closes == 1 and not owner.holds(stream.token)
    owner.submit({}).close()


def test_retirement_finishes_before_successor_is_admitted():
    entered, finish = threading.Event(), threading.Event()

    def close():
        entered.set()
        assert finish.wait(GUARD)

    worker = FakeWorker([FakeStream(on_close=close), FakeStream()])
    owner = StreamOwnership(worker)
    first = owner.submit({})
    waiter, outcome = launch(lambda: owner.submit({}))
    assert owner.await_waiters(timeout=GUARD)
    closer, closing = launch(first.close)
    assert entered.wait(GUARD)
    try:
        assert len(worker.calls) == 1 and not outcome
    finally:
        finish.set()
    join(closer)
    join(waiter)
    assert "error" not in closing and "error" not in outcome
    outcome["value"].close()


def test_close_failure_still_releases_and_preserves_callback_error():
    owner = StreamOwnership(FakeWorker([FakeStream(on_close=fail), FakeStream()]))
    first = owner.submit({})
    with pytest.raises(RuntimeError, match="callback broke"):
        first.close()
    owner.submit({}).close()
    raw = FakeStream([{"type": "result"}], on_close=fail)
    owner = StreamOwnership(FakeWorker([raw]))

    def callback(event):
        raise ValueError("original error")

    stream = owner.submit({}, event_callback=callback)
    with pytest.raises(ValueError, match="original error"):
        next(stream)
    assert raw.closes == 1 and not owner.holds(stream.token)


def test_shutdown_wakes_all_waiters_before_retirement_finishes():
    entered, finish = threading.Event(), threading.Event()

    def close():
        entered.set()
        assert finish.wait(GUARD)

    raw = FakeStream(on_close=close)
    owner = StreamOwnership(FakeWorker([raw]))
    active = owner.submit({})
    waiters = [launch(lambda: owner.submit({})) for _ in range(3)]
    assert owner.await_waiters(3, GUARD)
    stopper, stopped = launch(owner.shutdown)
    assert entered.wait(GUARD)
    try:
        for thread, outcome in waiters:
            join(thread)
            assert isinstance(outcome.get("error"), OwnershipClosed)
        with pytest.raises(OwnershipClosed):
            owner.submit({})
    finally:
        finish.set()
    join(stopper)
    assert "error" not in stopped and not owner.holds(active.token)
    owner.shutdown()
    assert raw.closes == 1


def test_shutdown_during_engine_submit_closes_returned_stream():
    entered, finish = threading.Event(), threading.Event()
    raw = FakeStream()

    class StartingWorker:
        def submit(self, request, **kwargs):
            entered.set()
            assert finish.wait(GUARD)
            return raw

    owner = StreamOwnership(StartingWorker())
    starter, started = launch(lambda: owner.submit({}))
    assert entered.wait(GUARD)
    waiter, waiting = launch(lambda: owner.submit({}))
    assert owner.await_waiters(timeout=GUARD)
    owner.shutdown()
    join(waiter)
    assert isinstance(waiting.get("error"), OwnershipClosed)
    finish.set()
    join(starter)
    assert isinstance(started.get("error"), OwnershipClosed) and raw.closes == 1


def test_shutdown_closure_failure_still_wakes_waiters():
    owner = StreamOwnership(FakeWorker([FakeStream(on_close=fail)]))
    active = owner.submit({})
    waiter, outcome = launch(lambda: owner.submit({}))
    assert owner.await_waiters(timeout=GUARD)
    with pytest.raises(RuntimeError, match="callback broke"):
        owner.shutdown()
    join(waiter)
    assert isinstance(outcome.get("error"), OwnershipClosed)
    assert not owner.holds(active.token)
    owner.shutdown()


def test_cancel_wakes_blocked_reader_and_cannot_affect_successor():
    entered, cancelled = threading.Event(), threading.Event()

    def read():
        entered.set()
        assert cancelled.wait(GUARD)

    raw = FakeStream([{"type": "result"}], on_read=read, on_close=cancelled.set)
    owner = StreamOwnership(FakeWorker([raw, FakeStream()]))
    first = owner.submit({})
    reader, outcome = launch(lambda: next(first))
    assert entered.wait(GUARD)
    assert first.cancel()
    successor = owner.submit({})
    join(reader)
    assert isinstance(outcome.get("error"), OwnershipClosed)
    assert owner.holds(successor.token)
    successor.close()


@pytest.mark.parametrize("held_mutex", ["wg", "engine"])
def test_cyclic_finalizer_queues_retirement_while_collecting_thread_holds_mutex(held_mutex):
    engine_lock = threading.Lock()
    closed_by = []

    def close():
        with engine_lock:
            closed_by.append(threading.get_ident())

    raw = FakeStream(on_close=close)
    owner = StreamOwnership(FakeWorker([raw, FakeStream()]))
    mutex = owner._changed if held_mutex == "wg" else engine_lock

    def collect():
        stream = owner.submit({})
        stream.cycle = stream
        with mutex:
            del stream
            gc.collect()

    collector, collected = launch(collect)
    join(collector)
    assert "error" not in collected
    waiter, waiting = launch(lambda: owner.submit({}))
    join(waiter)
    assert "error" not in waiting and raw.closes == 1
    assert closed_by and closed_by[0] != collector.ident
    waiting["value"].close()


def test_queued_finalizer_cannot_retire_successor():
    first, second = FakeStream(), FakeStream()
    owner = StreamOwnership(FakeWorker([first, second]))
    stream = owner.submit({})
    with owner._changed:
        stream.__del__()
        stream.__del__()
    waiter, outcome = launch(lambda: owner.submit({}))
    join(waiter)
    successor = outcome["value"]
    stream.close()
    assert first.closes == 1 and second.closes == 0 and owner.holds(successor.token)
    successor.close()


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_asynchronous_status_callback_failure_keeps_stderr_reader_alive_and_retires(error_type):
    engine_lock = threading.Lock()
    closed_by, calls = [], []
    failure = error_type("stderr callback broke")

    def close():
        with engine_lock:
            closed_by.append(threading.get_ident())

    raw = FakeStream(on_close=close)

    class StderrWorker(FakeWorker):
        def submit(self, request, *, status_callback=None, operation="solve"):
            self.callback = status_callback
            self.calls.append((request, operation))
            return next(self.streams)

        def stderr(self):
            with engine_lock:
                for message in ("first", "second", "third"):
                    self.callback(message)
            return "stderr drained"

    def callback(message):
        calls.append(message)
        raise failure

    worker = StderrWorker([raw, FakeStream()])
    owner = StreamOwnership(worker)
    stream = owner.submit({}, status_callback=callback)
    waiter, waiting = launch(lambda: owner.submit({}))
    assert owner.await_waiters(timeout=GUARD)
    reader, read = launch(worker.stderr)
    join(reader)
    join(waiter)
    assert read == {"value": "stderr drained"} and "error" not in waiting
    assert stream.callback_error is failure and calls == ["first"]
    assert raw.closes == 1 and closed_by[0] != reader.ident
    with pytest.raises(error_type, match="stderr callback broke"):
        next(stream)
    assert owner.holds(waiting["value"].token)
    waiting["value"].close()


@pytest.mark.parametrize("action", ["close", "cancel", "shutdown"])
def test_concurrent_close_cancel_shutdown_waits_for_retirement(monkeypatch, action):
    entered, finish, waiting = threading.Event(), threading.Event(), threading.Event()

    def close():
        entered.set()
        assert finish.wait(GUARD)

    raw = FakeStream(on_close=close)
    owner = StreamOwnership(FakeWorker([raw]))
    stream = owner.submit({})
    closer, closed = launch(stream.close)
    assert entered.wait(GUARD)
    original = owner._changed.wait_for

    def observe_wait(predicate, timeout=None):
        waiting.set()
        return original(predicate, timeout)

    monkeypatch.setattr(owner._changed, "wait_for", observe_wait)
    concurrent, outcome = launch(getattr(owner if action == "shutdown" else stream, action))
    try:
        assert waiting.wait(GUARD) and not outcome
    finally:
        finish.set()
    join(closer)
    join(concurrent)
    assert "error" not in closed and "error" not in outcome and raw.closes == 1


def test_concurrent_retirement_wait_has_bounded_timeout():
    entered, finish = threading.Event(), threading.Event()

    def close():
        entered.set()
        assert finish.wait(GUARD)

    raw = FakeStream(on_close=close)
    owner = StreamOwnership(FakeWorker([raw]), retirement_timeout_s=0.01)
    stream = owner.submit({})
    closer, outcome = launch(stream.close)
    assert entered.wait(GUARD)
    try:
        with pytest.raises(TimeoutError, match="retirement"):
            owner.shutdown()
    finally:
        finish.set()
    join(closer)
    assert "error" not in outcome and raw.closes == 1
    owner.shutdown()


def test_reentrant_close_during_retirement_does_not_wait_on_itself():
    raw = FakeStream()
    owner = StreamOwnership(FakeWorker([raw]))
    stream = owner.submit({})
    raw.on_close = stream.close
    closer, outcome = launch(stream.close)
    join(closer)
    assert "error" not in outcome and raw.closes == 1


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed"])
def test_cancel_racing_real_terminal_preserves_terminal_event(terminal):
    entered, finish = threading.Event(), threading.Event()

    def read():
        entered.set()
        assert finish.wait(GUARD)

    raw = FakeStream([{"type": terminal}], on_read=read)
    owner = StreamOwnership(FakeWorker([raw, FakeStream()]))
    stream = owner.submit({})
    reader, outcome = launch(lambda: next(stream))
    assert entered.wait(GUARD)
    stream.cancel()
    successor = owner.submit({})
    finish.set()
    join(reader)
    assert outcome == {"value": {"type": terminal}}
    assert owner.holds(successor.token) and raw.closes == 1
    successor.close()
