from __future__ import annotations

import asyncio
import threading
import time

import pytest

from server.solver.metal_permit import FieldEvaluationBusy, MetalPermit
from server.solver.watchdog import watch_native_operation


@pytest.mark.parametrize("cancel", [False, True])
def test_abandoned_native_thread_never_admits_another_operation(cancel):
    async def scenario():
        permit = MetalPermit()
        entered, release = threading.Event(), threading.Event()
        retained = set()
        stopped = []

        def native():
            entered.set()
            assert release.wait(5)
            return 42

        async def solve():
            async with permit.solve():
                return await watch_native_operation(
                    asyncio.to_thread(native), permit=permit, retained=retained,
                    last_progress=lambda: started, timeout_seconds=0.05,
                    stop_callbacks=lambda: stopped.append(True),
                )

        started = time.monotonic()
        first = asyncio.create_task(solve())
        try:
            while not entered.is_set():
                await asyncio.sleep(.001)
            pending = asyncio.create_task(permit.acquire_solve())
            await asyncio.sleep(0)
            if cancel:
                first.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                await first
            assert stopped == [True]
            assert len(retained) == 1
            with pytest.raises(FieldEvaluationBusy, match="Restart"):
                await pending
            with pytest.raises(FieldEvaluationBusy, match="Restart"):
                await permit.acquire_solve()
            with pytest.raises(FieldEvaluationBusy, match="Restart"):
                await permit.acquire_field("after-timeout")
            release.set()
            await asyncio.gather(*retained)
            await asyncio.sleep(0)
            assert not retained
            # A completed late thread still requires restart; release never resets it.
            with pytest.raises(FieldEvaluationBusy):
                await permit.acquire_solve()
        finally:
            release.set()
            await asyncio.gather(*retained, return_exceptions=True)

    asyncio.run(scenario())


def test_progress_extends_deadline_without_limiting_total_solve_duration():
    async def scenario():
        permit = MetalPermit()
        retained = set()
        progress = time.monotonic()

        async def native():
            nonlocal progress
            for _ in range(5):
                await asyncio.sleep(.015)
                progress = time.monotonic()
            return 42

        async with permit.solve():
            assert await watch_native_operation(
                native(), permit=permit, retained=retained,
                last_progress=lambda: progress, timeout_seconds=.04,
                stop_callbacks=lambda: pytest.fail("healthy operation stopped"),
            ) == 42
        assert permit.quarantine_reason is None
        lease = await permit.acquire_field("healthy")
        await lease.release()

    asyncio.run(scenario())


@pytest.mark.parametrize("raw", ["0", "-1", "inf", "nan", "invalid"])
def test_stall_limit_rejects_invalid_overrides(monkeypatch, raw):
    from server.solver.watchdog import STALL_TIMEOUT_ENV, stall_timeout_seconds
    monkeypatch.setenv(STALL_TIMEOUT_ENV, raw)
    with pytest.raises(ValueError):
        stall_timeout_seconds()


def test_adapter_timeout_after_work_finishes_does_not_quarantine():
    async def scenario():
        permit = MetalPermit()
        async def operation():
            raise TimeoutError("native worker was killed and reaped")
        with pytest.raises(TimeoutError, match="killed and reaped"):
            await watch_native_operation(
                operation(), permit=permit, retained=set(),
                last_progress=time.monotonic, timeout_seconds=1,
                stop_callbacks=lambda: pytest.fail("finished adapter quarantined"),
            )
        assert permit.quarantine_reason is None
    asyncio.run(scenario())
