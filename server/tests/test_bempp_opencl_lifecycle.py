"""Real probe processes: protocol rejection, reader drainage and shutdown ownership."""
import asyncio
import json
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

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
        child = original([sys.executable, '-c', script, argv[-1]], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(probe.subprocess, 'Popen', spawn)
    return children


@pytest.mark.parametrize('case', ['never_ready', 'duplicate_ready', 'early_exit', 'success_without_ready', 'stderr_flood', 'delayed_import'])
def test_real_reader_protocol(monkeypatch, case):
    monkeypatch.setattr(probe, 'SPAWN_IMPORT_SECONDS', 0.5)
    ready = f"print({probe._READY_MARKER!r}, flush=True); "
    result = f"import sys; open(sys.argv[1], 'w').write({json.dumps({'ok': True, 'smoke': {}})!r})"
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
    assert children[0].stderr.closed



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


@pytest.mark.parametrize("system", ["linux", "win32"])
def test_real_child_separates_interleaved_noise_from_protocol(monkeypatch, system):
    monkeypatch.setattr(probe.sys, "platform", system)
    script = f"""
import json, os, sys
# Exceeds both Windows/POSIX pipe buffers, then ends without a newline.
os.write(2, b'x' * 2000000)
os.write(2, b'70 warnings generated. unterminated compiler warning')
print({probe._READY_MARKER!r}, flush=True)
os.write(2, b'noise between READY and RESULT\\n')
# Library output can resemble the former protocol and still cannot affect JSON.
print('WG_OPENCL_RESULT garbage', flush=True)
print(' ' + {probe._READY_MARKER!r}, flush=True)
open(sys.argv[1], 'w').write(json.dumps({{"ok": True, "smoke": {{"solve_relative_error": 1e-7}}}}))
os.write(2, b'final compiler diagnostic')
"""
    children = child_for(monkeypatch, script)
    verdict = probe._run_probe("smoke", None, 5)
    assert verdict["ok"], verdict
    assert verdict["smoke"] == {"solve_relative_error": 1e-7}
    assert len(verdict["probe_stderr"]) == 4096
    assert verdict["probe_stderr"].endswith("final compiler diagnostic")
    assert children[0].stderr.closed
    assert children[0].stdout.closed
    assert not probe._active_probes


@pytest.mark.parametrize("case", ["crash", "garbage", "missing", "invalid_schema", "missing_reason"])
@pytest.mark.parametrize("mode", ["inventory", "smoke"])
def test_real_child_protocol_error_is_transient_and_retried(monkeypatch, case, mode):
    scripts = {
        "crash": "import os; os._exit(19)",
        "garbage": "import sys; open(sys.argv[1], 'w').write('not json')",
        "missing": "print('WG_OPENCL_RESULT garbage', flush=True)",
        "invalid_schema": "import sys; open(sys.argv[1], 'w').write('{\"ok\": true}')",
        "missing_reason": "import sys; open(sys.argv[1], 'w').write('{\"ok\": false, \"opencl_unavailable_reason\": \"smoke_test_failed\"}')",
    }
    children = child_for(monkeypatch, scripts[case])
    real_run = probe._run_probe
    def run(stage, device, timeout):
        if stage != mode:
            return {"ok": True, "devices": [{"platform_index": 0, "device_index": 0,
                    "type": "cpu", "platform": "CPU OpenCL", "name": "CPU", "vendor": "CPU"}]}
        return real_run(stage, device, timeout)
    monkeypatch.setattr(probe, "_run_probe", run)
    first = probe.qualified_opencl()
    assert first["opencl_unavailable_reason"] == "probe_error"
    assert "unusable" not in first["reason"]
    assert probe._cached_verdict is None
    assert not probe._device_verdict_cache
    assert probe.retry_pending()
    assert probe.qualified_opencl() == first
    # One immediate second run before a probe error is reported.
    assert len(children) == 2
    monkeypatch.setattr(probe, "_retry_after", 0)
    assert probe.qualified_opencl()["opencl_unavailable_reason"] == "probe_error"
    assert len(children) == 4


@pytest.mark.parametrize("mode", ["inventory", "smoke"])
def test_a_one_off_probe_error_ends_on_the_device_answer(monkeypatch, mode):
    """A check that fails once (a child that died while PoCL compiled, say)
    is run again at once, so a working device still ends on OpenCL."""
    good = (f"print({probe._READY_MARKER!r}, flush=True); import sys; open(sys.argv[1], 'w').write("
            + repr(json.dumps({"ok": True, "smoke": {}} if mode == "smoke" else {"ok": True, "devices": [
                {"platform_index": 0, "device_index": 0, "type": "cpu", "platform": "Portable Computing Language",
                 "name": "pthread", "vendor": "PoCL"}]})) + ")")
    scripts = iter(["import os; os._exit(-11 % 256)", good])
    children = []
    original_spawn = subprocess.Popen
    def spawn(argv, **kwargs):
        child = original_spawn([sys.executable, "-c", next(scripts), argv[-1]], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(probe.subprocess, "Popen", spawn)
    real_run = probe._run_probe
    def run(stage, device, timeout):
        if stage != mode:
            return {"ok": True, "smoke": {}} if stage == "smoke" else {"ok": True, "devices": [
                {"platform_index": 0, "device_index": 0, "type": "cpu", "platform": "Portable Computing Language",
                 "name": "pthread", "vendor": "PoCL"}]}
        return real_run(stage, device, timeout)
    monkeypatch.setattr(probe, "_run_probe", run)
    verdict = probe.qualified_opencl()
    assert verdict["ok"], verdict
    assert len(children) == 2


def test_a_probe_error_names_what_failed(monkeypatch):
    child_for(monkeypatch, "import os; os._exit(19)")
    verdict = probe._run_probe("smoke", None, 5)
    assert verdict["opencl_unavailable_reason"] == "probe_error"
    assert "internal error: ValueError: Child exited with status 19" in verdict["reason"]


@pytest.mark.parametrize("damage", ["wrong", "error"])
def test_real_probe_child_distinguishes_computation_from_internal_error(monkeypatch, damage):
    # Exercise the actual child exception classifier with hermetic imports.
    script = f"""
import sys, types
from pathlib import Path
from server.solver import bempp_opencl as probe
for name in ['bempp_cl', 'bempp_cl.api', 'bempp_cl.core', 'bempp_cl.core.opencl_kernels',
             'hornlab_bempp_bem', 'hornlab_bempp_bem.device', 'pyopencl']:
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
    parent, _, attr = name.rpartition('.')
    if parent:
        setattr(sys.modules[parent], attr, module)
def smoke(device):
    if {damage!r} == 'error':
        raise ValueError('unexpected check exception')
    return probe.check_computation(probe.reference_matrix() * 1.1)
probe.smoke_test = smoke
probe._write_probe_result(Path(sys.argv[1]), probe._child_result('smoke', None))
"""
    child_for(monkeypatch, script)
    verdict = probe._run_probe("smoke", None, 5)
    assert verdict["opencl_unavailable_reason"] == ("smoke_test_failed" if damage == "wrong" else "probe_error")
    assert "disagrees" in verdict["reason"] if damage == "wrong" else "ValueError" in verdict["reason"]



def test_real_probe_channel_belongs_to_the_session_and_is_cleaned(monkeypatch, tmp_path):
    from pathlib import Path
    from server.platform.temp_session import TemporarySession

    session = TemporarySession.create(tmp_path)
    session.activate()
    try:
        script = (f"print({probe._READY_MARKER!r}, flush=True); import sys; "
                  f"open(sys.argv[1], 'w').write({json.dumps({'ok': True, 'smoke': {}})!r})")
        children = child_for(monkeypatch, script)
        assert probe._run_probe("smoke", None, 5)["ok"]
        result_path = Path(children[0].args[-1])
        assert result_path.parent.parent == session.path
        assert not result_path.parent.exists()
    finally:
        session.close(remove=True)


def test_a_stop_leaves_no_probe_directory_in_the_session(monkeypatch, tmp_path):
    """The run removes its result directory after its child is reaped; a
    stop waits for that, so a clean stop leaves the session directory empty."""
    from server.platform.temp_session import TemporarySession

    session = TemporarySession.create(tmp_path)
    session.activate()
    owner = threading.Event()
    ready = threading.Event()
    original_reader = probe._read_probe_output
    def read(stream, events):
        class Observed:
            def put(self, item):
                events.put(item)
                if item[0] == probe._READY_MARKER:
                    ready.set()
        original_reader(stream, Observed())
    monkeypatch.setattr(probe, "_read_probe_output", read)
    child_for(monkeypatch, f"import time; print({probe._READY_MARKER!r}, flush=True); time.sleep(60)")
    outcome = []
    def qualify():
        try:
            outcome.append(probe.owned_qualification(owner, probe._run_probe, "smoke", None, 30))
        except probe.ProbeCancelled as exc:
            outcome.append(exc)
    worker = threading.Thread(target=qualify)
    try:
        worker.start()
        assert ready.wait(10), "child never printed READY"
        assert list(session.path.glob("wg2-opencl-*")), "the run should own a result directory"
        probe.shutdown_qualification(owner)
        assert not list(session.path.glob("wg2-opencl-*")), "a clean stop left the probe directory"
        assert not probe._active_channels
    finally:
        worker.join(10)
        session.close(remove=True)


def test_a_stop_removes_a_directory_its_run_did_not_get_to(monkeypatch, tmp_path):
    owner = threading.Event()
    channel = tempfile.TemporaryDirectory(prefix="wg2-opencl-", dir=tmp_path)
    monkeypatch.setattr(probe, "SHUTDOWN_CLEANUP_SECONDS", 0.2)
    with probe._probe_lock:
        probe._active_channels[channel] = owner
    try:
        started = time.monotonic()
        probe.shutdown_qualification(owner)
        assert time.monotonic() - started < 2
        assert not Path(channel.name).exists()
    finally:
        with probe._probe_lock:
            probe._active_channels.pop(channel, None)
