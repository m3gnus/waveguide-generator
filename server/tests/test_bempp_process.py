"""Process-boundary regression tests for responsive BEMPP cancellation."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import threading
import time

import pytest

from server.solver import bempp_process
from server.solver.bempp_process import _WARMUP_JOB_ID, BemppProcessHost
from server.solver.context import SolverContext


def _successful_worker(connection) -> None:
    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            job_id, _payload = command
            connection.send(("stage", job_id, ("frequency_solve", 0.5, "halfway")))
            connection.send(("result", job_id, (0, {"frequencies": [500.0]})))
            connection.send(("done", job_id, {"worker_pid": os.getpid()}))
    finally:
        connection.close()


def _blocking_worker(connection) -> None:
    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            job_id, _payload = command
            if job_id == _WARMUP_JOB_ID:
                connection.send(("warm", job_id, {"warmed": True}))
                continue
            connection.send(("stage", job_id, ("frequency_solve", 0.0, "blocked")))
            time.sleep(30.0)
    finally:
        connection.close()


def _echo_commands_worker(connection) -> None:
    """Report every job id it is handed, so a prewarm is observable."""

    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            job_id, _payload = command
            if job_id == _WARMUP_JOB_ID:
                connection.send(("warm", job_id, {"warmed": True, "pid": os.getpid()}))
                continue
            connection.send(("done", job_id, {"worker_pid": os.getpid()}))
    finally:
        connection.close()


def _large_result_worker(connection) -> None:
    """Return enough distinct floats to make pipe deserialization measurable."""

    try:
        job_id, _payload = connection.recv()
        connection.send(
            ("done", job_id, {"values": [float(index) for index in range(2_000_000)]})
        )
    finally:
        connection.close()


def _context() -> SolverContext:
    return SolverContext(
        design=None,
        frequency_range=(500.0, 1000.0),
        num_frequencies=2,
    )


def test_worker_stays_warm_and_forwards_ordered_events() -> None:
    async def exercise() -> None:
        host = BemppProcessHost(target=_successful_worker)
        stages: list[tuple[str, float, str]] = []
        results: list[tuple[int, dict]] = []
        try:
            first = await host.run(
                "mesh",
                _context(),
                mesh_metadata={},
                mesh_stats={},
                cancel_cb=lambda: None,
                stage_cb=lambda *event: stages.append(event),
                result_cb=lambda *event: results.append(event),
            )
            second = await host.run(
                "mesh",
                _context(),
                mesh_metadata={},
                mesh_stats={},
                cancel_cb=lambda: None,
                stage_cb=lambda *_event: None,
                result_cb=None,
            )
        finally:
            host.close()

        assert first["worker_pid"] == second["worker_pid"]
        assert stages == [("frequency_solve", 0.5, "halfway")]
        assert results == [(0, {"frequencies": [500.0]})]

    asyncio.run(exercise())


def test_cancellation_terminates_blocked_native_worker_promptly() -> None:
    class Cancelled(RuntimeError):
        pass

    async def exercise() -> tuple[float, BemppProcessHost]:
        host = BemppProcessHost(target=_blocking_worker)
        polls = 0

        def cancel() -> None:
            nonlocal polls
            polls += 1
            if polls >= 3:
                raise Cancelled("stop")

        started = time.monotonic()
        with pytest.raises(Cancelled, match="stop"):
            await host.run(
                "mesh",
                _context(),
                mesh_metadata={},
                mesh_stats={},
                cancel_cb=cancel,
                stage_cb=lambda *_event: None,
                result_cb=None,
            )
        return time.monotonic() - started, host

    elapsed, host = asyncio.run(exercise())
    try:
        assert elapsed < 1.5
        # The worker that was blocked is gone, and a replacement is already
        # warming. Leaving _process at None would push the whole native
        # initialization the kill just discarded onto the user's next solve --
        # 24.5 s on the reference Windows VM, measured with and without this.
        assert host._process is not None
        assert host._process.is_alive()
        assert host._warm_requested is True
    finally:
        host.close()


def test_cancellation_does_not_respawn_when_the_server_is_shutting_down() -> None:
    """asyncio.CancelledError is the app going away, not a user pressing Stop."""

    async def exercise() -> BemppProcessHost:
        host = BemppProcessHost(target=_blocking_worker)
        polls = 0

        def cancel() -> None:
            nonlocal polls
            polls += 1
            if polls >= 3:
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await host.run(
                "mesh",
                _context(),
                mesh_metadata={},
                mesh_stats={},
                cancel_cb=cancel,
                stage_cb=lambda *_event: None,
                result_cb=None,
            )
        return host

    host = asyncio.run(exercise())
    try:
        assert host._process is None
    finally:
        host.close()


def test_prewarm_starts_one_worker_and_is_idempotent() -> None:
    host = BemppProcessHost(target=_echo_commands_worker)
    try:
        host.prewarm()
        first = host._process
        assert first is not None and first.is_alive()
        assert host._warm_requested is True

        host.prewarm()
        assert host._process is first

        async def solve() -> dict:
            return await host.run(
                "mesh",
                _context(),
                mesh_metadata={},
                mesh_stats={},
                cancel_cb=lambda: None,
                stage_cb=lambda *_event: None,
                result_cb=None,
            )

        # The prewarm acknowledgement is still queued ahead of this solve's
        # events; run() has to step over it rather than fail the job on it.
        assert asyncio.run(solve())["worker_pid"] == first.pid
    finally:
        host.close()


def test_prewarm_after_close_starts_a_fresh_worker() -> None:
    """A second create_app in one interpreter must still get a warm worker."""

    host = BemppProcessHost(target=_echo_commands_worker)
    host.prewarm()
    first_pid = host._process.pid
    host.close()
    assert host._process is None
    try:
        host.prewarm()
        assert host._process is not None
        assert host._process.pid != first_pid
    finally:
        host.close()


def test_large_worker_result_is_deserialized_without_blocking_event_loop() -> None:
    async def exercise() -> tuple[dict, float]:
        host = BemppProcessHost(target=_large_result_worker)
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            previous = asyncio.get_running_loop().time()
            while not stop.is_set():
                await asyncio.sleep(0.005)
                current = asyncio.get_running_loop().time()
                gaps.append(current - previous)
                previous = current

        recv_threads: set[str] = set()
        original_poll_and_recv = bempp_process._poll_and_recv

        def recording_poll_and_recv(connection):
            recv_threads.add(threading.current_thread().name)
            return original_poll_and_recv(connection)

        bempp_process._poll_and_recv = recording_poll_and_recv
        ticker_task = asyncio.create_task(ticker())
        try:
            result = await host.run(
                "mesh",
                _context(),
                mesh_metadata={},
                mesh_stats={},
                cancel_cb=lambda: None,
                stage_cb=lambda *_event: None,
                result_cb=None,
            )
        finally:
            bempp_process._poll_and_recv = original_poll_and_recv
            stop.set()
            await ticker_task
            host.close()
        return result, max(gaps), recv_threads

    result, largest_gap, recv_threads = asyncio.run(exercise())

    assert len(result["values"]) == 2_000_000
    # What this test is really about: recv() unpickles the whole message, and
    # doing that on the loop stalled it 162 ms on a 30 MiB payload. Assert the
    # structural property -- every receive happened on a worker thread -- and
    # keep only a coarse responsiveness bound. A wall-clock threshold tight
    # enough to catch 162 ms is not separable from scheduling jitter on a
    # shared CI runner, where this ticker has been measured at 93 ms while the
    # loop was never blocked at all.
    assert recv_threads and "MainThread" not in recv_threads
    assert largest_gap < 1.0


def _drive_worker_in_thread(commands, expected, monkeypatch):
    """Run the real worker loop over a pipe, with the native warmup faked.

    The warmup is a 25-61 s native solve in a spawned process, which is neither
    fast nor patchable from the parent. The loop's decisions *about* it are
    both, so drive the loop in a thread and record what it decided.
    """

    from server.solver import bempp as bempp_module

    warmed: list[float] = []
    monkeypatch.setattr(
        bempp_process,
        "_warm_this_process",
        lambda: (warmed.append(time.monotonic()), {"warmed": True, "seconds": 0.0})[1],
    )
    # The worker imports this by name inside the loop, so patch it at its home.
    monkeypatch.setattr(
        bempp_module,
        "solve_bempp_from_msh_text",
        lambda *_args, **_kwargs: {"solved": True},
    )

    parent, child = multiprocessing.get_context("spawn").Pipe(duplex=True)
    for command in commands:
        parent.send(command)
    worker = threading.Thread(
        target=bempp_process._bempp_worker_main, args=(child,), daemon=True
    )
    worker.start()

    # Drain before shutting the loop down: the worker closes its end on exit,
    # and a closed Windows PipeConnection raises rather than replaying what is
    # still buffered in it.
    events = []
    deadline = time.monotonic() + 20.0
    while len(events) < expected and time.monotonic() < deadline:
        if parent.poll(0.1):
            events.append(parent.recv())
    parent.send(None)
    worker.join(20.0)
    assert not worker.is_alive()
    parent.close()
    return warmed, events


def test_worker_warms_when_nothing_else_is_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warmed, events = _drive_worker_in_thread([(_WARMUP_JOB_ID, None)], 1, monkeypatch)

    assert len(warmed) == 1
    assert events == [("warm", _WARMUP_JOB_ID, {"warmed": True, "seconds": 0.0})]


def test_worker_skips_a_warmup_a_real_solve_has_already_superseded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop, then immediately solve again: the user's own job is the warmup.

    Without this the eager respawn is a regression for the impatient case --
    the queued solve waits out a full stand-in solve before its own starts,
    measured at 24.9 s against 24.5 s for no respawn at all.
    """

    warmed, events = _drive_worker_in_thread(
        [(_WARMUP_JOB_ID, None), ("job-1", {"msh_text": "", "context": None})],
        2,
        monkeypatch,
    )

    assert warmed == []
    assert events[0] == (
        "warm",
        _WARMUP_JOB_ID,
        {"warmed": False, "detail": "superseded"},
    )
    assert events[1] == ("done", "job-1", {"solved": True})


def test_a_failed_warmup_is_reported_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host with no usable backend must still reach the user's job.

    Raising here would take the worker down before it ever served a solve, and
    would report the backend problem into a log nobody is reading instead of
    onto the job that hits it.
    """

    from server.solver import warmup as warmup_module

    def explode(_status):
        raise RuntimeError("no assembly backend")

    monkeypatch.setattr(warmup_module, "warm_bempp_in_this_process", explode)

    outcome = bempp_process._warm_this_process()

    assert outcome["warmed"] is False
    assert "no assembly backend" in outcome["detail"]


def test_the_worker_watchdog_is_inert_without_a_parent_process() -> None:
    """The loop tests drive _bempp_worker_main in-process; nothing may arm."""

    assert multiprocessing.parent_process() is None
    before = [thread for thread in threading.enumerate()]

    bempp_process._exit_when_parent_does()

    added = {thread.name for thread in threading.enumerate()} - {
        thread.name for thread in before
    }
    assert "wg2-bempp-parent-watch" not in added


def test_the_worker_leaves_when_its_parent_is_force_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A killed launcher runs no shutdown hook, so the worker must notice alone.

    Detached from the measuring process tree so nothing else tore it down, a
    worker whose parent was killed mid-warmup survived 22.8 s on the reference
    Windows VM -- it only finds out at its next recv(), which it does not reach
    until the warmup ends. With the watchdog it leaves in 0.27-0.31 s.
    """

    reader, writer = multiprocessing.get_context("spawn").Pipe(duplex=False)

    class _FakeParent:
        sentinel = reader

    monkeypatch.setattr(multiprocessing, "parent_process", lambda: _FakeParent)
    # os._exit is the point of the watchdog -- it does not wait for native code
    # to unwind -- so it has to be intercepted rather than allowed to run here.
    exits: list[int] = []
    monkeypatch.setattr(bempp_process.os, "_exit", exits.append)

    try:
        bempp_process._exit_when_parent_does()
        watchdog = next(
            thread
            for thread in threading.enumerate()
            if thread.name == "wg2-bempp-parent-watch"
        )

        assert exits == []
        writer.close()  # the parent goes away
        watchdog.join(20.0)

        assert not watchdog.is_alive()
        assert exits == [bempp_process._PARENT_GONE_EXIT_CODE]
    finally:
        reader.close()


def _worker_that_spawns_children(connection) -> None:
    """A worker body that does the one thing a split sweep does: fork workers.

    ``hornlab_bempp_bem`` splits a sweep with a ``ProcessPoolExecutor``. This
    stands in for that without importing bempp-cl, so it runs on hosted CI,
    which never runs real solvers.
    """

    from concurrent.futures import ProcessPoolExecutor

    try:
        with ProcessPoolExecutor(max_workers=2) as pool:
            squares = sorted(pool.map(abs, (-3, -4)))
        connection.send(("ok", squares))
    except BaseException as exc:  # noqa: BLE001 - the failure mode IS the subject
        connection.send(("failed", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def test_the_worker_can_have_children_because_a_split_sweep_needs_them() -> None:
    """The gap that let a broken default reach `main` and a release.

    ``main`` at `6070dab6` took the auto-split sweep default without the commit
    that made the worker non-daemonic, so every default solve of 80 or more
    frequencies died with "daemonic processes are not allowed to have children".
    CI was green throughout: the only test touching ``WG2_SOLVE_WORKERS``
    exercises env parsing, and nothing in the suite ever entered the
    multi-process path. A default that only breaks there is invisible to a suite
    that never goes there.

    This spawns the worker through the production code path and makes it create
    grandchildren, which is the whole of what was broken.
    """

    host = BemppProcessHost(target=_worker_that_spawns_children)
    try:
        connection = host._ensure_started()
        assert connection.poll(120), "the worker never reported back"
        status, payload = connection.recv()
        assert status == "ok", payload
        assert payload == [3, 4]
    finally:
        host._terminate_sync()


def test_a_daemonic_worker_could_not_have_had_children() -> None:
    """Prove the test above could have failed.

    Pinning ``daemon=False`` as a boolean would pass against any spawn, so it
    would not have caught this. Running the identical worker body under
    ``daemon=True`` shows the assertion above is load-bearing: the same code
    that succeeds through ``BemppProcessHost`` fails here, with the exact error
    users saw.
    """

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_worker_that_spawns_children, args=(child,), daemon=True
    )
    process.start()
    child.close()
    try:
        assert parent.poll(120), "the daemonic worker never reported back"
        status, payload = parent.recv()
        assert status == "failed"
        assert "daemonic processes are not allowed to have children" in payload
    finally:
        parent.close()
        process.join(30)
        if process.is_alive():  # pragma: no cover - defensive
            process.terminate()
