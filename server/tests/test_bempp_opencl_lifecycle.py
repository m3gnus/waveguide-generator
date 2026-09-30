"""Real probe processes: protocol rejection, reader drainage and shutdown ownership."""
import asyncio
import json
import subprocess
import sys
import threading
import time

import pytest

from server.engines import registry as reg
from server.solver import bempp_opencl as probe


@pytest.fixture(autouse=True)
def clean_probe():
    probe.clear_cache()
    yield
    probe.clear_cache()


def child_for(monkeypatch, script):
    original = subprocess.Popen
    children = []
    def spawn(argv, **kwargs):
        child = original([sys.executable, '-c', script], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(probe.subprocess, 'Popen', spawn)
    return children


@pytest.mark.parametrize('case', ['never_ready', 'duplicate_ready', 'early_exit', 'success_without_ready', 'stderr_flood', 'delayed_import'])
def test_real_reader_protocol(monkeypatch, case):
    monkeypatch.setattr(probe, 'SPAWN_IMPORT_SECONDS', 0.5)
    ready = f"print({probe._READY_MARKER!r}, flush=True); "
    result = f"print({(probe._RESULT_PREFIX + json.dumps({'ok': True}))!r}, flush=True)"
    scripts = {
        'never_ready': 'import time; time.sleep(30)',
        'duplicate_ready': 'import time; ' + ready + 'time.sleep(.20); ' + ready + 'time.sleep(.20); ' + result,
        'early_exit': 'import os; os._exit(19)',
        'success_without_ready': result,
        'stderr_flood': "import sys; sys.stderr.write('x' * 2000000 + '\\n'); " + ready + result,
        'delayed_import': 'import time; time.sleep(.20); ' + ready + 'time.sleep(.05); ' + result,
    }
    children = child_for(monkeypatch, scripts[case])
    start = time.monotonic()
    verdict = probe._run_probe('smoke', None, .30)
    elapsed = time.monotonic() - start
    assert elapsed < 3
    if case in {'stderr_flood', 'delayed_import'}:
        assert verdict['ok'], verdict
    else:
        assert not verdict['ok'], verdict
    if case == 'duplicate_ready':
        assert verdict['opencl_unavailable_reason'] == 'smoke_test_timeout'
    if case == 'never_ready':
        assert 'spawn/import' in verdict['reason']
    assert children[0].poll() is not None
    assert children[0].stdout.closed



@pytest.mark.parametrize("phase", ["initial", "retry"])
@pytest.mark.parametrize("force_kill", [False, True])
def test_registry_shutdown_reaps_real_child_and_reader(monkeypatch, phase, force_kill):
    from server.solver import bempp

    ready, readers, children = threading.Event(), [], []
    original_reader, original_spawn = probe._read_probe_output, subprocess.Popen
    script = f"import time; print({probe._READY_MARKER!r}, flush=True); time.sleep(60)"
    def spawn(argv, **kwargs):
        child = original_spawn([sys.executable, '-c', script], **kwargs)
        children.append(child)
        if force_kill:
            # Exercise the kill escalation on Windows as well, without signals.
            monkeypatch.setattr(child, 'terminate', lambda: None)
        return child
    def read(stream, events):
        readers.append(threading.current_thread())
        class ObservedEvents:
            def put(self, item):
                events.put(item)
                if item[0] == probe._READY_MARKER:
                    ready.set()
        original_reader(stream, ObservedEvents())
    monkeypatch.setattr(probe.subprocess, 'Popen', spawn)
    monkeypatch.setattr(probe, '_read_probe_output', read)
    def detector():
        if phase == 'initial':
            probe.qualified_opencl()
        return [reg.EngineInfo('bempp', True, 'temporary timeout', None,
                              assembly_backend='numba', opencl_unavailable_reason='inventory_timeout')]
    registry = reg.EngineRegistry(detector=detector, cpu_refresh=False)
    async def exercise():
        request = None
        try:
            if phase == 'initial':
                request = asyncio.create_task(registry.capabilities())
            else:
                await registry.capabilities()
                monkeypatch.setattr(bempp, 'bempp_status', probe.qualified_opencl)
                registry._bempp_refresh_task = asyncio.create_task(registry._refresh_bempp_timeout())
            assert await asyncio.to_thread(ready.wait, 3), 'real child never printed READY'
            child = children[0]
            assert child.returncode is None
            pid = child.pid
            started = time.monotonic()
            await asyncio.wait_for(registry.shutdown_prewarm(), 4)
            assert time.monotonic() - started < 4
            # returncode must already be set by shutdown's wait, before poll
            # could reap anything on behalf of the implementation.
            assert child.returncode is not None, f'child {pid} was not reaped'
            deadline = time.monotonic() + 0.5
            while child.poll() is None and time.monotonic() < deadline:
                await asyncio.sleep(0)
            assert child.poll() is not None, f'child {pid} is still alive'
            assert child.stdout.closed
            assert readers and all(not reader.is_alive() for reader in readers)
            assert not probe._active_probes
            assert probe._cached_verdict is None, 'shutdown must not cache a rejection'
            await asyncio.wait_for(registry.shutdown_prewarm(), 1)  # idempotent
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=2)
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)
    asyncio.run(exercise())


def test_shutdown_without_a_child_prevents_later_spawn(monkeypatch):
    owner = threading.Event()
    probe.shutdown_qualification(owner)
    probe.shutdown_qualification(owner)
    monkeypatch.setattr(probe.subprocess, 'Popen', lambda *a, **k: pytest.fail('spawn after shutdown'))
    with pytest.raises(probe.ProbeCancelled):
        probe.owned_qualification(owner, probe.qualified_opencl)
    assert not probe._active_probes
