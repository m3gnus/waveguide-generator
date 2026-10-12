"""Detect scheduler and CAD-lane spin if retirement leaks into update admission."""

from __future__ import annotations

import asyncio
import threading

import pytest

from server.engines.dryrun import DryRunEngine
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.runtime import JobRuntime
from server.updates.restart import RestartApproval

from cad_backends import JobsHarness
from test_cad_preparation import _received, _revision, _setup
from test_jobs_runtime import _request


class _BrokenApproval(RestartApproval):
    """The rejected design: route retirement also refuses scheduler admission."""

    def admit(self, start):
        if self.retirement_hold.held:
            return False, None
        return super().admit(start)

    def refusal(self):
        return super().refusal() or self.retirement_hold.refusal()


@pytest.mark.parametrize("broken", [False, True], ids=["route-only", "detector-catches-broken-admission"])
def test_retirement_does_not_spin_accepted_work(tmp_path, monkeypatch, broken):
    harness = JobsHarness(tmp_path)
    runtime = harness.runtime
    approval = _BrokenApproval() if broken else RestartApproval()
    started, release = threading.Event(), threading.Event()
    counts = {"jobs": 0, "drains": 0, "preparations": 0}

    class GatedDryRun(DryRunEngine):
        def solve(self, *args, **kwargs):
            started.set()
            assert release.wait(10), "test did not release the running solver"
            return super().solve(*args, **kwargs)

    runtime.engine_registry = EngineRegistry(
        detector=lambda: [EngineInfo("dryrun", True, "test", "test")],
        factory=lambda _name: GatedDryRun(),
    )
    runtime.restart_approval = approval
    approval.add_release_listener(runtime._restart_called_off)
    monkeypatch.setattr(runtime, "_ensure_scheduler", JobRuntime._ensure_scheduler.__get__(runtime))
    real_compose = runtime._compose_job

    async def compose(request, cad_provenance):
        # Reuse the CAD harness's jobs-system seam; solving the imported mesh is
        # outside this scheduler test, so its bound solve uses the dry-run fake.
        if cad_provenance:
            request = _request(delay_ms=0, count=2)
        return await real_compose(request, cad_provenance)

    monkeypatch.setattr(runtime, "_compose_job", compose)
    for name, key in (("_run_job", "jobs"), ("_drain_scheduler", "drains"), ("_run_cad_preparation", "preparations")):
        original = getattr(runtime, name)

        def wrapped(original=original, key=key):
            async def counted(*args, **kwargs):
                counts[key] += 1
                return await original(*args, **kwargs)
            return counted

        monkeypatch.setattr(runtime, name, wrapped())

    async def scenario():
        try:
            running = await runtime.submit(_request(delay_ms=0, count=2))
            assert await asyncio.to_thread(started.wait, 5)
            queued = await runtime.submit(_request(delay_ms=0, count=2))
            real_ensure_prep = runtime._ensure_prep_lane
            with monkeypatch.context() as patch:
                patch.setattr(runtime, "_ensure_prep_lane", lambda: None)
                _received(harness)
                revision = _revision(harness.store, _setup())
                preparing = await runtime.accept_cad_solve(
                    harness._intent("cmd-1", setup_revision_id=revision), "cad-solve:cmd-1",
                )
                assert runtime.store.get_job_row(running)["status"] == "running"
                assert runtime.store.get_job_row(queued)["status"] == "queued"
                assert runtime.store.get_job_row(preparing)["status"] == "preparing"
                approval.retirement_hold.hold("test")
            real_ensure_prep()
            await asyncio.sleep(0.05)
            assert runtime.store.get_job_row(running)["status"] == "running"
            release.set()
            # Both lanes yield to store reads, so this timer also bounds the broken case.
            await asyncio.sleep(2.0)
            snapshot = dict(counts)
            if broken:
                assert snapshot["jobs"] > 5, snapshot
                assert snapshot["preparations"] > 5, snapshot
            else:
                assert all(value <= 5 for value in snapshot.values()), snapshot
            approval.retirement_hold.release()
            await runtime.wait_cad_preparations(timeout=10)
            await runtime.wait_idle(timeout=10)
            assert all(runtime.store.get_job_row(job)["status"] == "complete" for job in (running, queued, preparing))
        finally:
            release.set()
            approval.retirement_hold.release()

    try:
        harness._loop.run(scenario(), timeout=30)
    finally:
        release.set()
        approval.retirement_hold.release()
        harness.close()
