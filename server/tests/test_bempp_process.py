"""Process-boundary regression tests for responsive BEMPP cancellation."""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from server.solver import bempp_process
from server.solver.bempp_process import BemppProcessHost
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
        job_id, _payload = connection.recv()
        connection.send(("stage", job_id, ("frequency_solve", 0.0, "blocked")))
        time.sleep(30.0)
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
        assert host._process is None
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
