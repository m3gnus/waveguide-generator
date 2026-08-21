"""Process-boundary regression tests for responsive BEMPP cancellation."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

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
