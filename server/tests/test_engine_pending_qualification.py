"""Startup snapshots and submission choices share one bounded qualification."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace as NS

import pytest

from server.diagnostics.capabilities import capabilities_payload
from server.engines.registry import EngineRegistry
from server.jobs.runtime import JobRuntime, resolve_submission
from server.jobs.store import JobStore
from server.solver import beat, bempp, bempp_opencl as probe, metal
from server.solver.base import EngineRunResult
from server.tests.test_bempp_opencl import CPU
from server.tests.test_engines_registry import _planner_request


@pytest.fixture
def pending_probe(monkeypatch):
    bempp.bempp_status.cache_clear()
    entered, release = threading.Event(), threading.Event()
    clock, calls = [0.0], []
    state = NS(outcome='opencl', metal=False, gpu=False)
    monkeypatch.setattr(probe, 'time', NS(monotonic=lambda: clock[0]))
    monkeypatch.setattr(bempp, '_load_api', lambda: True)
    monkeypatch.setattr(bempp, '_version', lambda: 'test')
    monkeypatch.setattr(bempp, '_probe_ground_plane_axes', lambda: ('y',))
    monkeypatch.setattr(bempp, '_probe_ground_plane_composition', lambda: True)
    monkeypatch.setattr(bempp, 'SolveConfig', lambda **_kwargs: NS())
    monkeypatch.setattr(metal, 'metal_status', lambda: {
        'available': state.metal, 'reason': 'Metal real state', 'version': 'test'})
    monkeypatch.setattr(beat, 'beat_backend_statuses', lambda: {
        backend: {'available': backend == 'cpu' or (backend == 'cuda' and state.gpu),
                  'reason': 'BEAT real state', 'version': 'test'}
        for backend in beat.BEAT_BACKENDS})
    def run(mode, device, timeout):
        calls.append((mode, timeout))
        if mode == 'inventory':
            entered.set()
            assert release.wait(5), 'test did not release the fake child'
            if state.outcome == 'numba':
                clock[0] += probe.SPAWN_IMPORT_SECONDS + probe.INVENTORY_SECONDS
                return {'ok': False, 'reason': 'inventory deadline',
                        'opencl_unavailable_reason': 'inventory_timeout'}
            if state.outcome == 'unavailable':
                return {'ok': False, 'stage': 'engine', 'reason': 'engine cannot load'}
            return {'ok': True, 'devices': [CPU], '_active_seconds': 0.01}
        return {'ok': True, 'smoke': {}, '_active_seconds': 0.01}
    monkeypatch.setattr(probe, '_run_probe', run)
    # Keep retries under explicit test control rather than a real timer.
    async def no_retry(_self):
        await asyncio.Future()
    monkeypatch.setattr(EngineRegistry, '_wait_opencl_retry', no_retry)
    yield NS(state=state, entered=entered, release=release, calls=calls)
    release.set()
    bempp.bempp_status.cache_clear()


async def startup(registry, pending):
    snapshot = await registry.capabilities()
    assert await asyncio.to_thread(pending.entered.wait, 5)
    rows = {item.name: item for item in snapshot}
    assert rows['bempp'].qualification == 'pending'
    assert rows['bempp'].assembly_backend is None
    assert rows['bempp'].opencl_unavailable_reason is None
    return rows


class RecordedEngine:
    def __init__(self, name, runs):
        self.name, self.runs = name, runs

    async def run(self, request, *, cancel_cb, stage_cb):
        cancel_cb()
        backend = probe.execution_route()[0] if self.name == 'bempp' else self.name
        self.runs.append((self.name, backend))
        stage_cb('frequency_solve', 1, 'Fake native solve')
        return EngineRunResult(results={
            'frequencies': [500.0], 'directivity': {},
            'spl_on_axis': {'frequencies': [500.0], 'spl': [90.0], 'phase_degrees': [0.0]},
            'impedance': {'frequencies': [500.0], 'real': [1.0], 'imaginary': [0.0]},
            'di': {'frequencies': [500.0], 'di': {}},
            'metadata': {'engine': self.name, 'assembly_backend': backend},
        })


@pytest.mark.parametrize('engine', ['metal', 'beat-cuda', 'beat-cpu'])
def test_other_engine_publishes_and_runs_without_qualification_wait(pending_probe, tmp_path, engine):
    p = pending_probe
    p.state.metal = p.state.gpu = True
    runs = []
    registry = EngineRegistry(cpu_refresh=False, factory=lambda name: RecordedEngine(name, runs))
    async def exercise():
        runtime = JobRuntime(JobStore(tmp_path / 'jobs.db'), engine_registry=registry)
        try:
            rows = await startup(registry, p)
            assert rows[engine].available
            assert rows[engine].reason == ('Metal real state' if engine == 'metal' else 'BEAT real state')
            assert rows[engine].qualification is None
            payload = await capabilities_payload(registry)
            assert next(row for row in payload['engines'] if row['name'] == engine)['available']
            job_id = await asyncio.wait_for(runtime.submit(_planner_request(engine=engine)), 5)
            await runtime.wait_idle()
            assert (await runtime.get_job(job_id))['status'] == 'complete'
            assert runs == [(engine, engine)]
            assert not p.release.is_set()
            assert not registry._initial_bempp_task.done()
        finally:
            p.release.set()
            await registry.wait_for_bempp()
            await runtime.shutdown()
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


@pytest.mark.parametrize('outcome', ['opencl', 'numba'])
@pytest.mark.parametrize('requested', ['bempp', 'auto'])
def test_pending_bempp_submission_waits_then_completes(pending_probe, monkeypatch, tmp_path, outcome, requested):
    p = pending_probe
    p.state.outcome = outcome
    runs, waiting = [], asyncio.Event()
    registry = EngineRegistry(cpu_refresh=False, factory=lambda name: RecordedEngine(name, runs))
    real_wait = registry.wait_for_bempp
    async def observed_wait():
        waiting.set()
        return await real_wait()
    monkeypatch.setattr(registry, 'wait_for_bempp', observed_wait)
    async def exercise():
        runtime = JobRuntime(JobStore(tmp_path / 'jobs.db'), engine_registry=registry)
        try:
            await startup(registry, p)
            payload = await capabilities_payload(registry)
            assert payload['engineSelection']['resolvedDefault'] is None
            submission = asyncio.create_task(runtime.submit(_planner_request(engine=requested)))
            await asyncio.wait_for(waiting.wait(), 5)
            assert not submission.done()
            assert not runs
            assert not p.release.is_set()
            p.release.set()
            job_id = await asyncio.wait_for(submission, 5)
            await runtime.wait_idle()
            job = await runtime.get_job(job_id)
            assert job['status'] == 'complete'
            assert runs == [('bempp', outcome)]
            result = await runtime.get_results(job_id)
            assert result['metadata']['assembly_backend'] == outcome
            metadata = result['metadata']['symmetry']['solver_plan']
            assert 'engine_substitution' not in metadata
            assert [mode for mode, _ in p.calls] == (['inventory', 'smoke'] if outcome == 'opencl' else ['inventory'])
            assert next(item for item in await registry.capabilities() if item.name == 'bempp').qualification == 'done'
        finally:
            p.release.set()
            await registry.wait_for_bempp()
            await runtime.shutdown()
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


@pytest.mark.parametrize('above', ['metal', 'beat-cuda'])
def test_auto_takes_compatible_higher_engine_immediately(pending_probe, above):
    p = pending_probe
    p.state.metal = above == 'metal'
    p.state.gpu = above == 'beat-cuda'
    registry = EngineRegistry(cpu_refresh=False, factory=lambda _name: object())
    async def exercise():
        try:
            await startup(registry, p)
            resolved = await asyncio.wait_for(resolve_submission(_planner_request(), registry), 5)
            assert resolved.engine_name == above
            assert not p.release.is_set()
            assert not registry._initial_bempp_task.done()
        finally:
            p.release.set()
            await registry.wait_for_bempp()
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


@pytest.mark.parametrize('outcome,expected', [('opencl', 'bempp'), ('numba', 'bempp'), ('unavailable', 'beat-cpu')])
def test_auto_choice_is_invariant_during_and_after_qualification(pending_probe, monkeypatch, outcome, expected):
    p = pending_probe
    p.state.outcome = outcome
    waiting = asyncio.Event()
    registry = EngineRegistry(cpu_refresh=False, factory=lambda _name: object())
    real_wait = registry.wait_for_bempp
    async def observed_wait():
        waiting.set()
        return await real_wait()
    monkeypatch.setattr(registry, 'wait_for_bempp', observed_wait)
    async def exercise():
        try:
            await startup(registry, p)
            request = _planner_request()
            during = asyncio.create_task(resolve_submission(request, registry))
            await asyncio.wait_for(waiting.wait(), 5)
            assert not during.done(), 'AUTO must not pass pending BEMPP for BEAT CPU'
            p.release.set()
            first = await asyncio.wait_for(during, 5)
            after = await resolve_submission(request, registry)
            assert first.engine_name == after.engine_name == expected
        finally:
            p.release.set()
            await registry.wait_for_bempp()
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


def test_auto_waits_when_higher_available_engine_cannot_solve_mounting(pending_probe, monkeypatch):
    p = pending_probe
    p.state.gpu = True  # CUDA is available, but refuses coupled infinite baffle.
    waiting = asyncio.Event()
    registry = EngineRegistry(cpu_refresh=False, factory=lambda _name: object())
    real_wait = registry.wait_for_bempp
    async def observed_wait():
        waiting.set()
        return await real_wait()
    monkeypatch.setattr(registry, 'wait_for_bempp', observed_wait)
    async def exercise():
        try:
            await startup(registry, p)
            task = asyncio.create_task(resolve_submission(_planner_request(sim_type='infinite-baffle'), registry))
            await asyncio.wait_for(waiting.wait(), 5)
            assert not task.done()
            p.release.set()
            assert (await task).engine_name == 'bempp'
        finally:
            p.release.set()
            await registry.wait_for_bempp()
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


@pytest.mark.parametrize('outcome,expected', [('opencl', 'bempp'), ('numba', 'beat-cpu')])
def test_imported_auto_waits_then_keeps_same_choice_when_bempp_may_be_ineligible(pending_probe, monkeypatch, outcome, expected):
    from server.jobs.runtime import resolve_imported_submission
    from server.tests.test_imported_jobs import _request

    p = pending_probe
    p.state.outcome = outcome
    waiting = asyncio.Event()
    registry = EngineRegistry(cpu_refresh=False, factory=lambda _name: object())
    real_wait = registry.wait_for_bempp
    async def observed_wait():
        waiting.set()
        return await real_wait()
    monkeypatch.setattr(registry, 'wait_for_bempp', observed_wait)
    async def exercise():
        try:
            await startup(registry, p)
            request = _request('wgi_' + '0' * 26)
            request.options.engine = 'auto'
            during = asyncio.create_task(resolve_imported_submission(request, registry))
            await asyncio.wait_for(waiting.wait(), 5)
            assert not during.done()
            p.release.set()
            first = await asyncio.wait_for(during, 5)
            after = await resolve_imported_submission(request, registry)
            assert first.engine_name == after.engine_name == expected
            if outcome == 'numba':
                assert 'bempp: does not declare imported geometry' in first.symmetry_metadata['solver_plan']['eligibility_reasons']
        finally:
            p.release.set()
            await registry.wait_for_bempp()
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


def test_cancelled_bempp_waiter_does_not_cancel_shared_check(pending_probe, monkeypatch):
    p = pending_probe
    waiting = asyncio.Event()
    registry = EngineRegistry(cpu_refresh=False, factory=lambda _name: object())
    real_wait = registry.wait_for_bempp
    async def observed_wait():
        waiting.set()
        return await real_wait()
    monkeypatch.setattr(registry, 'wait_for_bempp', observed_wait)
    async def exercise():
        try:
            await startup(registry, p)
            cancelled = asyncio.create_task(registry.get_engine('bempp'))
            await asyncio.wait_for(waiting.wait(), 5)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert not registry._initial_bempp_task.done()
            waiting.clear()
            survivor = asyncio.create_task(registry.get_engine('bempp'))
            await asyncio.wait_for(waiting.wait(), 5)
            assert not survivor.done()
            p.release.set()
            assert await asyncio.wait_for(survivor, 5) is not None
            assert [mode for mode, _ in p.calls] == ['inventory', 'smoke']
        finally:
            p.release.set()
            await registry.wait_for_bempp()
            await registry.shutdown_prewarm()
    asyncio.run(exercise())
