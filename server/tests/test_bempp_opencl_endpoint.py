"""Qualification recovery through the real app's HTTP and startup paths."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace as NS

import pytest

from server import app as app_module
from server.engines import registry as registry_module
from server.solver import beat, bempp, bempp_opencl as probe, metal, warmup
from server.tests.test_app_batch_e import TestClient
from server.tests.test_bempp_opencl import CPU


@pytest.mark.parametrize('recovery', ['request', 'idle'])
@pytest.mark.parametrize('code', ['inventory_timeout', 'probe_error'])
def test_endpoint_recovers_during_startup_without_restart(monkeypatch, tmp_path, recovery, code):
    bempp.bempp_status.cache_clear()
    # This test keeps the real app's lifespan open long enough for the WGLink
    # startup pass to run. It must not touch the user's Fusion add-in, nor leave
    # a process-wide activation report behind for later tests.
    monkeypatch.setenv('WG2_WGLINK_REFRESH', '0')
    async def no_addin_refresh(*_args, **_kwargs):
        pass
    for name in ('start_addin_refresh', 'shutdown_addin_refresh'):
        monkeypatch.setattr(app_module, name, no_addin_refresh)
    clock, calls = [0.0], []
    monkeypatch.setattr(probe, 'time', NS(monotonic=lambda: clock[0]))
    monkeypatch.setattr(bempp, '_load_api', lambda: True)
    # Mesh warmup is unrelated native work with a process-global gmsh thread;
    # retain the actual solver prewarm lifecycle under test.
    async def no_mesh_warmup():
        pass
    for name in ('prewarm_gmsh_worker', 'shutdown_gmsh_worker',
                 'prewarm_mesher', 'shutdown_mesher_prewarm'):
        monkeypatch.setattr(app_module, name, no_mesh_warmup)
    # Real worker_prewarm schedules the app's import/native warm boundary on
    # another thread. Hold it in flight while qualification competes with it.
    import_started, release_import = threading.Event(), threading.Event()
    probe_started, release_probe = threading.Event(), threading.Event()
    def startup_import(_engine):
        import_started.set()
        assert release_import.wait(5)
        return True
    monkeypatch.setattr(warmup, 'persisted_engine_preference', lambda _settings: 'bempp')
    monkeypatch.setattr(warmup, 'prewarm_bempp_worker_for_engine', startup_import)
    def run(mode, device, timeout):
        assert threading.current_thread() is not threading.main_thread()
        calls.append(mode)
        if mode == 'inventory' and calls.count('inventory') == 1:
            assert import_started.wait(5), 'qualification must overlap app prewarm'
            probe_started.set()
            assert release_probe.wait(5)
            return {'ok': False, 'reason': 'slow inventory after ready',
                    'opencl_unavailable_reason': code}
        if mode == 'inventory' and code == 'probe_error' and calls.count('inventory') == 2:
            # A probe error is run again at once; keep that run failing too, so
            # this test still exercises recovery by the later retry.
            return {'ok': False, 'reason': 'still failing', 'opencl_unavailable_reason': code}
        return {'ok': True, 'devices': [CPU]} if mode == 'inventory' else {'ok': True, 'smoke': {}}
    monkeypatch.setattr(probe, '_run_probe', run)
    monkeypatch.setattr(metal, 'metal_status', lambda: {
        'available': True, 'reason': 'Metal ready', 'version': 'test'})
    monkeypatch.setattr(beat, 'beat_backend_statuses', lambda: {
        backend: {'available': backend == 'cpu', 'reason': 'BEAT detected', 'version': 'test'}
        for backend in beat.BEAT_BACKENDS})
    # A zero injected HTTP wait exercises the timeout branch deterministically
    # while the real qualification thread is held by events, not timed sleeps.
    assert registry_module.CAPABILITIES_WAIT_SECONDS <= probe.TOTAL_SECONDS
    monkeypatch.setattr(registry_module, 'CAPABILITIES_WAIT_SECONDS', 0)

    async def exercise():
        retry_permission = asyncio.Event()
        async def wait_retry(_self):
            await retry_permission.wait()
        monkeypatch.setattr(registry_module.EngineRegistry, '_wait_opencl_retry', wait_retry)
        application = app_module.create_app(data_dir=tmp_path)
        registry = application.state.engine_registry
        client = TestClient(application)
        def row(response):
            assert response.status_code == 200
            return next(e for e in response.json()['engines'] if e['name'] == 'bempp')
        async with application.router.lifespan_context(application):
            try:
                assert await asyncio.to_thread(probe_started.wait, 5)
                await registry._initial_probe_task
                other = next(item for item in registry._cache if item.name == 'metal')
                pending = row(await asyncio.wait_for(client.request_async('GET', '/api/capabilities'), 5))
                assert not release_probe.is_set() and not release_import.is_set()
                assert pending['available'] is False
                assert pending['assembly_backend'] is None
                assert pending['opencl_unavailable_reason'] is None
                assert pending['qualification'] == 'pending'
                assert 'Checking OpenCL' in pending['reason']
                rows = (await client.request_async('GET', '/api/capabilities')).json()['engines']
                assert all(set(item) == set(pending) for item in rows)
                assert all(item['label'] for item in rows)
                assert next(item for item in rows if item['name'] == 'metal')['available'] is True
                assert next(item for item in rows if item['name'] == 'beat-cpu')['available'] is True
                # Completing the first timeout publishes numba; qualification stays
                # alive when the first HTTP wait expires.
                release_probe.set()
                await registry.wait_for_bempp()
                monkeypatch.setattr(registry_module, 'CAPABILITIES_WAIT_SECONDS', 30)
                first = row(await client.request_async('GET', '/api/capabilities'))
                assert first['opencl_retry_pending'] is True
                assert first['qualification'] == 'done'
                assert first['assembly_backend'] == 'numba'
                assert first['opencl_unavailable_reason'] == code
                if code == 'probe_error':
                    assert first['reason'] == ("WG's OpenCL check could not complete (internal error); "
                                               "using the slower numba engine for now and retrying.")
                assert tuple(first['geometry_sources']) == ('parametric',)
                # A probe error is run again at once before it is reported.
                first_runs = ['inventory'] * (2 if code == 'probe_error' else 1)
                assert calls == first_runs
                assert registry._opencl_retry_task is not None
                clock[0] += probe.RETRY_INTERVAL_SECONDS
                if recovery == 'idle':
                    retry_permission.set()
                    # No endpoint or solve triggers this attempt: prewarm owns it.
                    await asyncio.wait_for(registry._opencl_retry_task, 5)
                    assert next(item for item in registry._cache if item.name == 'bempp').assembly_backend == 'opencl'
                if recovery == 'request':
                    assert row(await client.request_async('GET', '/api/capabilities'))['qualification'] == 'pending'
                    await registry.wait_for_bempp()
                recovered = row(await client.request_async('GET', '/api/capabilities'))
                assert set(recovered) == set(pending)
                assert recovered['label']
                assert recovered['opencl_retry_pending'] is False
                assert recovered['qualification'] == 'done'
                assert recovered['assembly_backend'] == 'opencl'
                assert recovered['assembly_device'] == CPU
                assert recovered['opencl_unavailable_reason'] is None
                assert 'imported' in recovered['geometry_sources']
                assert await registry.resolve('auto', solver_mode=None, mounting='infinite-baffle') == 'metal'
                assert await registry.resolve('bempp', solver_mode=None) == 'bempp'
                assert probe.execution_route() == ('opencl', CPU)
                assert calls == first_runs + ['inventory', 'smoke']
                assert next(item for item in registry._cache if item.name == 'metal') is other
            finally:
                release_probe.set()
                release_import.set()
    try:
        asyncio.run(exercise())
    finally:
        release_probe.set()
        release_import.set()
        bempp.bempp_status.cache_clear()
