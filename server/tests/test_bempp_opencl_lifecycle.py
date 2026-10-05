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
from server.tests._symlinks import requires_symlinks


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


@pytest.mark.parametrize("mode", ["inventory", "smoke"])
def test_parent_rejects_a_crash_even_after_a_complete_success_report(monkeypatch, mode):
    result = ({"ok": True, "devices": []} if mode == "inventory" else
              {"ok": True, "smoke": {"matrix_relative_error": 0., "solve_relative_error": 0.}})
    script = f"""
import os, sys
from pathlib import Path
from server.solver import bempp_opencl as probe
print(probe._READY_MARKER, flush=True)
probe._write_probe_result(Path(sys.argv[1]), {result!r})
os.abort()
"""
    children = child_for(monkeypatch, script)
    verdict = probe._run_probe(mode, None, 5)
    assert children[0].returncode != 0
    assert not verdict["ok"]
    assert verdict["opencl_unavailable_reason"] == "probe_error"
    assert "Child exited with status" in verdict["reason"]


@pytest.mark.parametrize("mode", ["inventory", "smoke"])
def test_completed_native_child_exits_before_teardown_abort(tmp_path, mode):
    result = ({"ok": True, "devices": []} if mode == "inventory" else
              {"ok": True, "smoke": {"matrix_relative_error": 0., "solve_relative_error": 0.}})
    path = tmp_path / "result.json"
    script = f"""
import atexit, os
from pathlib import Path
from server.solver import bempp_opencl as probe
atexit.register(os.abort)
probe._child_result = lambda *args: {result!r}
print('completed computation', end='')
probe._child_main({mode!r}, None, Path({str(path)!r}))
"""
    child = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5)
    assert child.returncode == 0, child.stderr
    assert child.stdout == "completed computation"
    assert json.loads(path.read_text()) == result
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("damage", ["rejected", "malformed", "write", "stdout", "stderr"])
def test_child_cannot_take_success_exit_after_a_failed_completion(tmp_path, damage):
    path = tmp_path / "result.json"
    script = f"""
import atexit, os, sys
from pathlib import Path
from server.solver import bempp_opencl as probe
atexit.register(lambda: os._exit(23))
result = {{"ok": True, "smoke": {{"matrix_relative_error": 0., "solve_relative_error": 0.}}}}
if {damage!r} == 'rejected':
    result = {{"ok": False, "opencl_unavailable_reason": "smoke_test_failed", "reason": "wrong answer"}}
elif {damage!r} == 'malformed':
    result = {{"ok": True}}
elif {damage!r} == 'write':
    def fail(*args):
        raise OSError('cannot publish result')
    probe._write_probe_result = fail
elif {damage!r} in ('stdout', 'stderr'):
    class BrokenFlush:
        def __init__(self, stream):
            self.stream = stream
        def write(self, value):
            return self.stream.write(value)
        def flush(self):
            raise OSError('cannot flush probe output')
    name = {damage!r}
    setattr(sys, name, BrokenFlush(getattr(sys, name)))
probe._child_result = lambda *args: result
probe._child_main('smoke', None, Path({str(path)!r}))
"""
    child = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5)
    assert child.returncode != 0, (damage, child.stdout, child.stderr)
    if damage in {"malformed", "write"}:
        assert not path.exists()
    elif damage == "rejected":
        assert json.loads(path.read_text())["ok"] is False


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


@pytest.mark.parametrize("mode", ["inventory", "smoke"])
def test_the_immediate_second_run_spends_the_same_budget(monkeypatch, mode):
    """A probe error's second run gets only what the first left of its
    budget, so qualification keeps its documented active-time ceiling."""
    timeouts = []
    budget = min(probe.INVENTORY_SECONDS, probe.TOTAL_SECONDS) if mode == "inventory" else min(
        probe.PROBE_SECONDS, probe.TOTAL_SECONDS)
    first_active = 0.6 * budget
    def run(stage, device, timeout):
        if stage != mode:
            return {"ok": True, "devices": [{"platform_index": 0, "device_index": 0, "type": "cpu",
                    "platform": "CPU OpenCL", "name": "CPU", "vendor": "CPU"}], "_active_seconds": 0.0}
        timeouts.append(timeout)
        return {"ok": False, "opencl_unavailable_reason": "probe_error", "reason": "internal error",
                "_active_seconds": first_active if len(timeouts) == 1 else 1.0}
    monkeypatch.setattr(probe, "_run_probe", run)
    probe.qualified_opencl()
    assert len(timeouts) == 2
    assert timeouts[0] == pytest.approx(budget)
    assert timeouts[1] == pytest.approx(budget - first_active)
    assert first_active + timeouts[1] <= budget + 1e-9


def test_no_second_run_when_the_first_spent_the_budget(monkeypatch):
    calls = []
    def run(stage, device, timeout):
        calls.append((stage, timeout))
        return {"ok": False, "opencl_unavailable_reason": "probe_error", "reason": "internal error",
                "_active_seconds": timeout}
    monkeypatch.setattr(probe, "_run_probe", run)
    assert probe.qualified_opencl()["opencl_unavailable_reason"] == "probe_error"
    assert [stage for stage, _ in calls] == ["inventory"]


def test_a_stop_refuses_a_run_before_it_makes_its_directory(monkeypatch, tmp_path):
    """The directory is made and registered under the lock a stop takes, so
    a stop never returns while an unregistered directory exists."""
    made, locked, registered_at_creation = [], [], []
    real = tempfile.TemporaryDirectory
    def directory(*args, **kwargs):
        locked.append(probe._probe_lock.locked())
        made.append(real(*args, dir=tmp_path, **{k: v for k, v in kwargs.items() if k != "dir"}))
        return made[-1]
    monkeypatch.setattr(probe.tempfile, "TemporaryDirectory", directory)
    def no_spawn(*args, **kwargs):
        registered_at_creation.append(made[-1] in probe._active_channels)
        raise OSError("no child in this test")
    monkeypatch.setattr(probe.subprocess, "Popen", no_spawn)
    # An ordinary run: the directory is made under the lock and is already
    # registered by the time anything else happens.
    assert probe._run_probe("smoke", None, 5)["opencl_unavailable_reason"] == "probe_error"
    assert locked == [True] and registered_at_creation == [True]
    assert not list(tmp_path.iterdir()) and not probe._active_channels
    # After a stop, a run is refused before it makes anything.
    owner = threading.Event()
    probe.shutdown_qualification(owner)
    token = probe._probe_owner.set(owner)
    try:
        with pytest.raises(probe.ProbeCancelled):
            probe._run_probe("smoke", None, 5)
    finally:
        probe._probe_owner.reset(token)
    assert len(made) == 1 and not list(tmp_path.iterdir())
    assert not probe._active_channels


def test_a_spawned_worker_puts_its_probe_directory_in_the_servers_session(monkeypatch, tmp_path):
    """A BEMPP solve worker is spawned without the server's session active; it
    is handed the session at spawn, so its probe result directory is removed
    with the session, never left loose in the temporary directory."""
    from server.platform import temp_session

    session = tmp_path / "wg2-run-123-abc"
    session.mkdir()
    (session / temp_session.OWNER_LOCK_NAME).write_text("")
    monkeypatch.setattr(temp_session.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(temp_session, "_active_root", None)
    monkeypatch.setattr(temp_session, "_parent_root", None)
    temp_session.adopt_parent_session(str(session))
    assert temp_session.spawned_directory_root() == str(session)
    script = (f"print({probe._READY_MARKER!r}, flush=True); import sys; "
              f"open(sys.argv[1], 'w').write({json.dumps({'ok': True, 'smoke': {}})!r})")
    children = child_for(monkeypatch, script)
    assert probe._run_probe("smoke", None, 5)["ok"]
    assert Path(children[0].args[-1]).parent.parent == session
    # Anything but an existing session directory is ignored.
    temp_session.adopt_parent_session(str(tmp_path / "elsewhere"))
    assert temp_session.spawned_directory_root() is None


@requires_symlinks
def test_only_a_real_session_is_adopted(monkeypatch, tmp_path):
    """A wg2-run-* symlink to an unrelated directory, a directory without its
    owner lock, or one outside the temporary directory is never adopted: the
    probe would create where no session cleanup or sweep removes it."""
    from server.platform import temp_session

    base = tmp_path / "tmp"
    base.mkdir()
    monkeypatch.setattr(temp_session.tempfile, "tempdir", str(base))
    monkeypatch.setattr(temp_session, "_active_root", None)
    monkeypatch.setattr(temp_session, "_parent_root", None)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / temp_session.OWNER_LOCK_NAME).write_text("")
    link = base / "wg2-run-1-link"
    link.symlink_to(unrelated, target_is_directory=True)
    unlocked = base / "wg2-run-2-unlocked"
    unlocked.mkdir()
    elsewhere = tmp_path / "wg2-run-3-elsewhere"
    elsewhere.mkdir()
    (elsewhere / temp_session.OWNER_LOCK_NAME).write_text("")
    real = base / "wg2-run-4-real"
    real.mkdir()
    (real / temp_session.OWNER_LOCK_NAME).write_text("")
    for refused in (link, unlocked, elsewhere):
        temp_session.adopt_parent_session(str(refused))
        assert temp_session.spawned_directory_root() is None, refused
    temp_session.adopt_parent_session(str(real))
    assert temp_session.spawned_directory_root() == str(real)


def test_a_worker_whose_session_is_gone_makes_nothing(monkeypatch, tmp_path):
    """A worker that outlives the server's session (the server is stopping)
    refuses the probe rather than writing loose in the system temp directory."""
    from server.platform import temp_session

    monkeypatch.setattr(temp_session.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(temp_session, "_active_root", None)
    session = tmp_path / "wg2-run-5-gone"
    session.mkdir()
    (session / temp_session.OWNER_LOCK_NAME).write_text("")
    monkeypatch.setattr(temp_session, "_parent_root", None)
    temp_session.adopt_parent_session(str(session))
    (session / temp_session.OWNER_LOCK_NAME).unlink()
    session.rmdir()
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *a, **k: pytest.fail("spawn without a session"))
    with pytest.raises(probe.ProbeCancelled):
        probe._run_probe("smoke", None, 5)
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("wg2-opencl-")]
    assert not probe._active_channels


def test_a_held_probe_directory_is_retried_then_logged(monkeypatch, tmp_path, caplog):
    """Windows can refuse to delete a just-closed or scanned file for a moment.
    Removal retries; one that never succeeds is logged, not swallowed."""
    monkeypatch.setattr(probe, "REMOVE_RETRY_SECONDS", 0.0)
    class Held:
        def __init__(self, failures):
            self.failures, self.calls = failures, 0
            self.name = str(tmp_path / f"wg2-opencl-held{failures}")
            Path(self.name).mkdir()
        def cleanup(self):
            self.calls += 1
            if self.calls <= self.failures:
                raise PermissionError(13, "The process cannot access the file", self.name)
            Path(self.name).rmdir()
    brief = Held(3)
    probe._remove_channel(brief)
    assert brief.calls == 4 and not Path(brief.name).exists()
    stuck = Held(10 ** 6)
    with caplog.at_level("WARNING", logger=probe.__name__):
        probe._remove_channel(stuck)
    assert stuck.calls == probe.REMOVE_ATTEMPTS and Path(stuck.name).exists()
    assert "Could not remove the OpenCL check's directory" in caplog.text


def _stub_bempp_api(monkeypatch, scratch):
    """``bempp_cl.api`` as far as its import-time scratch directory goes."""
    import types
    api = types.SimpleNamespace(TMP_PATH=str(scratch))
    monkeypatch.setitem(sys.modules, "bempp_cl", types.SimpleNamespace(api=api))
    monkeypatch.setitem(sys.modules, "bempp_cl.api", api)
    return api


def test_frequency_pool_workers_relocate_import_time_scratch(monkeypatch, tmp_path):
    from server.platform import temp_session

    sweep = pytest.importorskip("hornlab_bempp_bem.sweep")
    monkeypatch.setattr(sweep, "ProcessPoolExecutor", sweep.ProcessPoolExecutor)
    monkeypatch.setattr(temp_session.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(temp_session, "_active_root", None)
    monkeypatch.setattr(temp_session, "_parent_root", None)
    session = temp_session.TemporarySession.create(tmp_path)
    temp_session.adopt_parent_session(str(session.path))
    api = _stub_bempp_api(monkeypatch, session.path)
    made = {}

    def executor(**kwargs):
        made.update(kwargs)
        return "pool"

    monkeypatch.setattr(probe, "ProcessPoolExecutor", executor)
    try:
        def native_sweep(**_kwargs):
            return sweep.ProcessPoolExecutor(max_workers=2, mp_context="spawn")

        assert probe.native_call(native_sweep, assembly_backend="numba", opencl_device="cpu") == "pool"
        assert made["max_workers"] == 2 and made["mp_context"] == "spawn"
        assert made["initargs"][0] == str(session.path)
        # A fresh spawn imports bempp and creates a new, empty system tmp dir.
        scratch = tmp_path / "tmpbempp-pool"
        scratch.mkdir()
        api.TMP_PATH = str(scratch)
        temp_session.adopt_parent_session(None)
        assert temp_session.spawned_directory_root() is None
        made["initializer"](*made["initargs"])
        assert temp_session.temporary_directory_root() is None
        assert api.TMP_PATH == str(session.path) and not scratch.exists()
        assert session.path.is_dir()
    finally:
        session.close(remove=True)


def _frequency_worker_scratch_root():
    import bempp_cl.api as api
    from server.platform import temp_session

    return api.TMP_PATH, temp_session.spawned_directory_root(), temp_session.temporary_directory_root()


def test_a_real_spawn_pool_adopts_the_solve_workers_session(monkeypatch):
    import multiprocessing

    api = pytest.importorskip("bempp_cl.api")
    sweep = pytest.importorskip("hornlab_bempp_bem.sweep")
    from server.platform import temp_session

    monkeypatch.setattr(sweep, "ProcessPoolExecutor", sweep.ProcessPoolExecutor)
    monkeypatch.setattr(temp_session, "_active_root", None)
    monkeypatch.setattr(temp_session, "_parent_root", None)
    session = temp_session.TemporarySession.create()
    temp_session.adopt_parent_session(str(session.path))
    monkeypatch.setattr(api, "TMP_PATH", str(session.path))
    try:
        pool = probe.native_call(
            lambda **_kwargs: sweep.ProcessPoolExecutor(
                max_workers=1, mp_context=multiprocessing.get_context("spawn"),
            ),
            assembly_backend="numba", opencl_device="cpu",
        )
        with pool:
            result = pool.submit(_frequency_worker_scratch_root).result(timeout=30)
        assert result == (str(session.path), str(session.path), None)
    finally:
        session.close(remove=True)


def test_bempps_import_time_scratch_directory_moves_into_the_session(monkeypatch, tmp_path):
    """``import bempp_cl.api`` makes ``TMP_PATH = tempfile.mkdtemp()`` and never
    removes it: one empty ``tmp*`` per process in the system temporary directory
    (five after one start and a solve in the 0.3.4 Windows rehearsal)."""
    from server.platform.temp_session import TemporarySession
    scratch = tmp_path / "system" / "tmpbempp"
    scratch.mkdir(parents=True)
    api = _stub_bempp_api(monkeypatch, scratch)
    session = TemporarySession.create(tmp_path)
    session.activate()
    try:
        probe._keep_bempp_scratch_in_session()
        assert api.TMP_PATH == str(session.path) and not scratch.exists()
        probe._keep_bempp_scratch_in_session()
        assert api.TMP_PATH == str(session.path) and session.path.is_dir()
    finally:
        session.close(remove=True)


def test_bempps_scratch_directory_is_left_alone_when_used_or_without_a_session(monkeypatch, tmp_path):
    from server.platform.temp_session import TemporarySession
    scratch = tmp_path / "system" / "tmpbempp"
    scratch.mkdir(parents=True)
    api = _stub_bempp_api(monkeypatch, scratch)
    probe._keep_bempp_scratch_in_session()
    assert api.TMP_PATH == str(scratch) and scratch.is_dir()
    (scratch / "sphere.msh").write_text("bempp's own\n", encoding="utf-8")
    session = TemporarySession.create(tmp_path)
    session.activate()
    try:
        probe._keep_bempp_scratch_in_session()
        assert api.TMP_PATH == str(scratch) and (scratch / "sphere.msh").is_file()
    finally:
        session.close(remove=True)


def test_the_check_child_makes_its_temporary_directories_in_its_channel(monkeypatch, tmp_path):
    """The parent removes the channel however the child ends, so whatever the
    child's imports leave in its temporary directory goes with it."""
    channel = tmp_path / "wg2-opencl-check"
    channel.mkdir()
    seen = []
    monkeypatch.setattr(tempfile, "tempdir", None)
    def result(mode, device):
        seen.append(tempfile.gettempdir())
        return {"ok": False, "opencl_unavailable_reason": "probe_error", "reason": "stub"}
    monkeypatch.setattr(probe, "_child_result", result)
    probe._child_main("inventory", None, channel / "result.json")
    assert seen == [str(channel)]
    assert json.loads((channel / "result.json").read_text(encoding="utf-8"))["ok"] is False
