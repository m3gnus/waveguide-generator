"""An enumerable ICD is not evidence that a BEMPP kernel computed correctly."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from server.solver import bempp_opencl as probe
from server.platform import process_tree, temp_session


_RESULT_PREFIX = "TEST_RESULT "

CPU = {"platform_index": 1, "device_index": 0, "type": "cpu", "platform": "CPU OpenCL", "vendor": "CPU", "name": "CPU", "fp64": True}
GPU = {"platform_index": 0, "device_index": 0, "type": "gpu", "platform": "Apple", "vendor": "Apple", "name": "M1 GPU", "fp64": False}


@pytest.fixture(autouse=True)
def clear_cache(probe_session):
    probe.clear_cache()
    yield
    probe.clear_cache()


@pytest.fixture
def probe_session(tmp_path_factory, monkeypatch):
    """Parent-side probe tests need the session a launched server owns."""
    native_platform = sys.platform
    monkeypatch.setattr(temp_session, "_active_root", None)
    monkeypatch.setattr(temp_session, "_parent_root", None)
    session = temp_session.TemporarySession.create(tmp_path_factory.mktemp("opencl-session"))
    session.activate()
    try:
        yield session
    finally:
        # Tests may emulate Windows; release the lock on its actual platform.
        with monkeypatch.context() as cleanup:
            cleanup.setattr(sys, "platform", native_platform)
            session.close(remove=True)


@pytest.mark.parametrize("constant,value", [
    ("SPAWN_IMPORT_SECONDS", 80), ("TOTAL_SECONDS", 40),
    ("MAX_TIMEOUT_ATTEMPTS", 4), ("RETRY_INTERVAL_SECONDS", 7),
])
def test_published_qualification_budget_tracks_limits(monkeypatch, constant, value):
    import asyncio
    from server.diagnostics.capabilities import capabilities_payload
    from server.engines.registry import EngineInfo

    class Snapshot:
        async def capabilities(self):
            return (EngineInfo("bempp", False, "Checking OpenCL…", None,
                               qualification="pending"),)

    original = asyncio.run(capabilities_payload(Snapshot()))["opencl_qualification_max_seconds"]
    assert original == 460.0
    monkeypatch.setattr(probe, constant, value)
    expected = (probe.MAX_TIMEOUT_ATTEMPTS * (2 * probe.SPAWN_IMPORT_SECONDS + probe.TOTAL_SECONDS)
                + (probe.MAX_TIMEOUT_ATTEMPTS - 1) * probe.RETRY_INTERVAL_SECONDS)
    published = asyncio.run(capabilities_payload(Snapshot()))["opencl_qualification_max_seconds"]
    assert published == expected
    assert published != original


def test_inventory_excludes_every_vendor_gpu(monkeypatch):
    def device(name, kind, vendor):
        return NS(name=name, vendor=vendor, type=kind, extensions="", double_fp_config=0)
    platforms = [
        NS(name=vendor, get_devices=lambda vendor=vendor: [device(vendor, 4, vendor)])
        for vendor in ("AMD", "NVIDIA", "Intel", "Apple")
    ]
    platforms.append(NS(name="PoCL", get_devices=lambda: [device("CPU", 2, "PoCL")]))
    monkeypatch.setitem(sys.modules, "pyopencl", NS(get_platforms=lambda: platforms, device_type=NS(CPU=2, GPU=4)))
    ranked = probe.rank_devices(probe.inventory())
    assert [d["type"] for d in ranked] == ["cpu"]
    assert ranked[0]["platform_index"] == 4
    assert not any(d["fp64"] for d in ranked)


@pytest.mark.parametrize("failed_cpu", [False, True])
def test_qualified_route_ranking_and_cached_verdict(monkeypatch, failed_cpu):
    calls = []
    def run(mode, device, timeout):
        calls.append((mode, device, timeout))
        if mode == "inventory":
            return {"ok": True, "devices": [GPU, CPU]}
        if device == CPU and failed_cpu:
            return {"ok": False, "reason": "kernel computed zeros"}
        return {"ok": True, "smoke": {"solve_relative_error": 1e-7}}
    monkeypatch.setattr(probe, "_run_probe", run)
    result = probe.qualified_opencl()
    assert result["ok"] is not failed_cpu
    assert result.get("device") == (None if failed_cpu else CPU)
    assert result["opencl_unavailable_reason"] == ("smoke_test_failed" if failed_cpu else None)
    assert probe.qualified_opencl() == result
    assert [c[1] for c in calls[1:]] == [CPU]
    assert calls[0][2] == probe.INVENTORY_SECONDS
    assert all(0 < c[2] <= probe.PROBE_SECONDS for c in calls[1:])
    # A device is not retried even under a different remaining time budget.
    probe._device_verdict(json.dumps(CPU, sort_keys=True), 0.1)
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["zero", "wrong", "timeout"])
def test_no_device_passes_means_no_opencl(monkeypatch, failure):
    def run(mode, device, timeout):
        return {"ok": True, "devices": [CPU, GPU]} if mode == "inventory" else {"ok": False, "reason": failure, "opencl_unavailable_reason": "smoke_test_timeout" if failure == "timeout" else "smoke_test_failed"}
    monkeypatch.setattr(probe, "_run_probe", run)
    result = probe.qualified_opencl()
    assert result["ok"] is False
    assert "device" not in result
    assert failure in result["reason"]
    assert result["opencl_unavailable_reason"] == ("smoke_test_timeout" if failure == "timeout" else "smoke_test_failed")


def test_total_time_budget_is_bounded(monkeypatch):
    calls = []
    def run(mode, device, timeout):
        calls.append((mode, timeout))
        return ({"ok": True, "devices": [CPU, {**CPU, "device_index": 1}], "_active_seconds": 9.0}
                if mode == "inventory" else
                {"ok": False, "reason": "timeout", "_active_seconds": timeout})
    monkeypatch.setattr(probe, "_run_probe", run)
    assert not probe.qualified_opencl()["ok"]
    assert calls == [("inventory", probe.INVENTORY_SECONDS), ("smoke", 20.0), ("smoke", 1.0)]


def fake_children(monkeypatch, scripts):
    """Replay stdout and process completion on an injected monotonic clock.

    Reader runs synchronously in this fixture, but timestamps/queue waits have
    the same semantics as the real thread. No sleep depends on CI speed.
    """
    import queue
    clock, children = [0.0], []
    scripts = iter(scripts)
    monkeypatch.setattr(probe, "time", NS(monotonic=lambda: clock[0]))
    class Events:
        def __init__(self):
            self.items = []
        def put(self, item):
            self.items.append(item)
        def get(self, timeout):
            if not self.items or self.items[0][1] > clock[0] + timeout:
                clock[0] += timeout
                raise queue.Empty
            item = self.items.pop(0)
            clock[0] = max(clock[0], item[1])
            return item
    class Reader:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args
        def start(self):
            began = clock[0]
            self.target(*self.args)
            clock[0] = began
        def join(self, timeout=None):
            pass
        def is_alive(self):
            return False
    class Stream:
        def __init__(self, script):
            self.script = script
            self.closed = False
            self.lines = iter(script)
            self.path = None
        def readline(self, limit):
            try:
                delay, line = next(self.lines)
            except StopIteration:
                return ""
            clock[0] += delay
            if line.startswith(_RESULT_PREFIX):
                self.path.write_text(line[len(_RESULT_PREFIX):], encoding="utf-8")
                return "child finished\n"
            return line + "\n"
        def read(self, limit):
            return ""
        def close(self):
            self.closed = True
    class Child:
        def __init__(self, argv, **kwargs):
            assert kwargs["stderr"] == subprocess.PIPE
            assert kwargs["stdout"] == subprocess.PIPE
            assert kwargs["env"]["NUMBA_DISABLE_JIT"] == "1"
            assert kwargs["env"]["PYOPENCL_COMPILER_OUTPUT"] == "0"
            self.args, self.returncode = argv, None
            self.stdout = Stream(next(scripts))
            self.stdout.path = Path(argv[-1])
            self.stderr = Stream([])
            self.killed, self.waited = False, False
            children.append(self)
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            self.waited = True
            self.returncode = -9 if self.killed else 0
            return self.returncode
        def kill(self):
            self.killed = True
    monkeypatch.setattr(probe.subprocess, "Popen", Child)
    # A replayed child has no process to contain or resume.
    monkeypatch.setattr(process_tree, "popen_in_windows_job",
                        lambda command, *, subject, **kwargs: (Child(command, **kwargs), None))
    monkeypatch.setattr(probe.queue, "Queue", Events)
    # Do not patch threading globally: endpoint tests still use real workers.
    monkeypatch.setattr(probe, "threading", NS(Thread=Reader, Lock=__import__("threading").Lock))
    return clock, children


def child_script(verdict, *, import_seconds=0.0, compute_seconds=0.0):
    verdict = {"devices": [], "smoke": {}, "opencl_unavailable_reason": "smoke_test_failed", **verdict}
    return [(import_seconds, probe._READY_MARKER),
            (compute_seconds, _RESULT_PREFIX + json.dumps(verdict))]


def test_multiple_cpu_imports_share_published_wall_budget(monkeypatch):
    devices = [{**CPU, "device_index": index} for index in range(4)]
    clock, children = fake_children(monkeypatch, [
        child_script({"ok": True, "devices": devices}, import_seconds=58, compute_seconds=9),
        *[child_script({"ok": False, "reason": "bad computation"},
                       import_seconds=58, compute_seconds=1) for _ in devices],
    ])
    verdict = probe.qualified_opencl()
    assert verdict["opencl_unavailable_reason"] == "smoke_test_timeout"
    assert clock[0] == 2 * probe.SPAWN_IMPORT_SECONDS + probe.TOTAL_SECONDS
    assert len(children) == 3
    assert all(child.waited and child.stdout.closed for child in children)
    assert children[-1].killed


@pytest.mark.parametrize("mode", ["inventory", "smoke"])
def test_import_time_is_not_charged_to_compute_deadline(monkeypatch, mode):
    timeout = probe.INVENTORY_SECONDS if mode == "inventory" else probe.PROBE_SECONDS
    clock, children = fake_children(monkeypatch, [child_script(
        {"ok": True, "devices": [CPU], "smoke": {}},
        import_seconds=timeout + 5, compute_seconds=1)])
    verdict = probe._run_probe(mode, CPU, timeout)
    assert verdict["ok"]
    assert verdict["_active_seconds"] == 1
    assert clock[0] == timeout + 6
    assert children[0].waited and not children[0].killed and children[0].stdout.closed


@pytest.mark.parametrize("mode,code", [("inventory", "inventory_timeout"), ("smoke", "smoke_test_timeout")])
@pytest.mark.parametrize("phase", ["import", "compute"])
def test_subprocess_timeout_reason_distinguishes_stage(monkeypatch, mode, code, phase):
    timeout = probe.INVENTORY_SECONDS if mode == "inventory" else probe.PROBE_SECONDS
    script = child_script({"ok": True},
                          import_seconds=probe.SPAWN_IMPORT_SECONDS + 1 if phase == "import" else 1,
                          compute_seconds=timeout + 1 if phase == "compute" else 0)
    clock, children = fake_children(monkeypatch, [script])
    verdict = probe._run_probe(mode, CPU, timeout)
    assert verdict["opencl_unavailable_reason"] == code
    assert code in probe.OPENCL_UNAVAILABLE_REASONS
    assert clock[0] == (probe.SPAWN_IMPORT_SECONDS if phase == "import" else timeout + 1)
    assert children[0].killed and children[0].waited and children[0].stdout.closed
    assert ("spawn/import" if phase == "import" else "compute") in verdict["reason"]


def test_total_budget_excludes_imports_in_both_children(monkeypatch):
    fake_children(monkeypatch, [
        child_script({"ok": True, "devices": [CPU]}, import_seconds=40, compute_seconds=9),
        child_script({"ok": True, "smoke": {}}, import_seconds=40, compute_seconds=19),
    ])
    assert probe.qualified_opencl()["device"] == CPU


@pytest.mark.parametrize("output", ["not json", _RESULT_PREFIX + "invalid", _RESULT_PREFIX + "[]"])
def test_crashed_or_malformed_probe_is_rejected(monkeypatch, output):
    fake_children(monkeypatch, [[(0, probe._READY_MARKER), (0, output)]])
    assert probe._run_probe("smoke", CPU, 1)["opencl_unavailable_reason"] == "probe_error"


@pytest.mark.parametrize("damage", ["zero", "nan", "partial", "wrong", "shape"])
def test_smoke_rejects_zero_partial_nonfinite_and_wrong_computation(damage):
    matrix = probe.reference_matrix().copy()
    if damage == "zero":
        matrix[:] = 0  # PoCL Windows: enumeration works, kernels compute nothing.
    elif damage == "nan":
        matrix[0, 0] = np.nan
    elif damage == "partial":
        np.fill_diagonal(matrix, 0)  # regular worked, singular did not
    elif damage == "wrong":
        matrix *= 1.1  # non-zero and finite, still incorrect
    else:
        matrix = matrix[:4, :4]
    with pytest.raises((RuntimeError, np.linalg.LinAlgError)):
        probe.check_computation(matrix)


def test_smoke_accepts_single_precision_roundoff():
    result = probe.check_computation(probe.reference_matrix().astype(np.complex64))
    assert result["matrix_relative_error"] < 1e-6
    assert result["solve_relative_error"] < 1e-6


def test_cpu_is_bound_before_single_precision_bempp_assembly(monkeypatch):
    events = []
    selected = NS(name=CPU["name"], vendor=CPU["vendor"], platform=NS(name=CPU["platform"]), type=2)
    def bind(platform, device):
        events.append(("bind", platform, device))
    def operator(*args, **kwargs):
        assert events[0] == ("bind", 1, 0)
        assert events[-1] == ("configure", "cpu")
        assert kwargs["precision"] == "single"
        assert kwargs["device_interface"] == "opencl"
        assert kwargs["parameters"].quadrature.regular == 4
        return NS(weak_form=lambda: NS(to_dense=lambda: probe.reference_matrix()))
    api = NS(
        set_default_cpu_device=bind,
        shapes=NS(regular_sphere=lambda level: "octahedron"),
        function_space=lambda *args: "DP0",
        DefaultParameters=lambda: NS(quadrature=NS()),
        operators=NS(boundary=NS(helmholtz=NS(single_layer=operator))),
    )
    monkeypatch.setitem(sys.modules, "pyopencl", NS(device_type=NS(CPU=2, GPU=4), get_platforms=lambda: [None, NS(get_devices=lambda: [selected])]))
    monkeypatch.setitem(sys.modules, "bempp_cl.api", api)
    if "bempp_cl" in sys.modules:
        monkeypatch.setattr(sys.modules["bempp_cl"], "api", api)
    monkeypatch.setitem(sys.modules, "bempp_cl.core.opencl_kernels", NS(default_cpu_device=lambda: selected))
    monkeypatch.setitem(sys.modules, "hornlab_bempp_bem.device", NS(
        reset_opencl_device=lambda: events.append(("reset",)),
        configure_opencl=lambda kind: events.append(("configure", kind)),
    ))
    assert probe.smoke_test(CPU)["solve_relative_error"] == 0
    assert events.count(("bind", 1, 0)) == 1


def test_changed_inventory_does_not_bind_an_unqualified_device(monkeypatch):
    other = NS(name="Other CPU", vendor="CPU", platform=NS(name="PoCL"), type=2)
    monkeypatch.setitem(sys.modules, "pyopencl", NS(device_type=NS(CPU=2, GPU=4), get_platforms=lambda: [None, NS(get_devices=lambda: [other])]))
    monkeypatch.setitem(sys.modules, "bempp_cl.api", NS(set_default_cpu_device=lambda *a: None))
    monkeypatch.setitem(sys.modules, "bempp_cl.core.opencl_kernels", NS(default_cpu_device=lambda: NS(name="Other CPU", vendor="CPU", platform=NS(name="PoCL"), type=2)))
    with pytest.raises(RuntimeError, match="inventory changed"):
        probe.bind_device(CPU)


@pytest.mark.parametrize("reason,code", [("enumerates but computes zero", "pocl_windows"), ("wrong non-zero answer", "smoke_test_failed"), ("smoke timed out", "smoke_test_timeout")])
def test_failed_compute_routes_to_numba_with_notice(monkeypatch, reason, code):
    from server.solver import bempp
    bempp.bempp_status.cache_clear()
    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp, "qualified_opencl", lambda: {"ok": False, "reason": reason, "opencl_unavailable_reason": code})
    try:
        status = bempp.bempp_status()
        assert status["available"]
        assert status["assembly_backend"] == "numba"
        assert status["assembly_device"] is None
        assert status["opencl_unavailable_reason"] == code
        assert reason in status["warning"]
        assert "correct but slow" in status["warning"]
    finally:
        bempp.bempp_status.cache_clear()


def test_zero_compute_is_rejected_and_gpu_is_never_smoke_tested(monkeypatch):
    def run(mode, device, timeout):
        if mode == "inventory":
            return {"ok": True, "devices": [CPU, GPU]}
        assert device == CPU
        try:
            matrix = np.zeros((8, 8))
            return {"ok": True, "smoke": probe.check_computation(matrix)}
        except RuntimeError as exc:
            return {"ok": False, "reason": str(exc)}
    monkeypatch.setattr(probe, "_run_probe", run)
    result = probe.qualified_opencl()
    assert not result["ok"]
    assert result["opencl_unavailable_reason"] == "smoke_test_failed"


@pytest.mark.parametrize("failure", [False, True])
def test_several_cpus_are_ranked_stably_and_failed_cpu_is_skipped(monkeypatch, failure):
    second = {**CPU, "device_index": 1, "vendor": "AMD", "name": "Ryzen",
              "platform": "Intel(R) OpenCL"}
    assert probe.rank_devices([GPU, CPU, second]) == [CPU, second]
    calls = []
    def run(mode, device, timeout):
        if mode == "inventory":
            return {"ok": True, "devices": [GPU, CPU, second]}
        calls.append(device)
        if device == CPU and failure:
            return {"ok": False, "reason": "bad computation"}
        return {"ok": True, "smoke": probe.check_computation(probe.reference_matrix())}
    monkeypatch.setattr(probe, "_run_probe", run)
    result = probe.qualified_opencl()
    assert result["device"] == (second if failure else CPU)
    assert calls == ([CPU, second] if failure else [CPU])


def test_gpu_only_host_falls_back_to_numba_without_smoke(monkeypatch):
    from server.solver import bempp
    bempp.bempp_status.cache_clear()
    def run(mode, device, timeout):
        assert mode == "inventory"  # Any GPU smoke invocation must fail this test.
        return {"ok": True, "devices": [GPU]}
    monkeypatch.setattr(probe, "_run_probe", run)
    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    try:
        status = bempp.bempp_status()
        assert status["assembly_backend"] == "numba"
        assert status["assembly_device"] is None
        assert status["opencl_unavailable_reason"] == "no_device"
        assert "A GPU OpenCL device does not substitute" in status["reason"]
        assert "correct but slow" in status["warning"]
    finally:
        bempp.bempp_status.cache_clear()


@pytest.mark.parametrize("entry", ["bind_device", "smoke_test"])
def test_gpu_cannot_be_bound_or_smoke_tested(entry):
    with pytest.raises(RuntimeError, match="refuses GPU"):
        getattr(probe, entry)(GPU)


@pytest.mark.parametrize("system", ["win32", "linux", "darwin"])
@pytest.mark.parametrize("platform", ["PoCL", "Portable Computing Language", "CPU OpenCL"])
def test_pocl_smoke_failure_classification_per_os(monkeypatch, system, platform):
    monkeypatch.setattr(probe.sys, "platform", system)
    # The device name is deliberately misleading: only its platform identifies PoCL.
    device = {**CPU, "platform": platform, "name": "PoCL CPU"}
    monkeypatch.setattr(probe, "_run_probe", lambda mode, *args:
                        {"ok": True, "devices": [device]} if mode == "inventory"
                        else {"ok": False, "reason": "zero computation"})
    code = "pocl_windows" if system == "win32" and platform != "CPU OpenCL" else "smoke_test_failed"
    assert probe.qualified_opencl()["opencl_unavailable_reason"] == code


@pytest.mark.parametrize("inventory_result,code", [
    ({"ok": True, "devices": []}, "no_device"),
    ({"ok": False, "reason": "no ICD"}, "no_device"),
    ({"ok": False, "reason": "hung", "opencl_unavailable_reason": "inventory_timeout"}, "inventory_timeout"),
])
def test_inventory_failure_reason(monkeypatch, inventory_result, code):
    monkeypatch.setattr(probe, "_run_probe", lambda *args: inventory_result)
    assert probe.qualified_opencl()["opencl_unavailable_reason"] == code



def test_inventory_changed_to_gpu_is_refused_before_binding(monkeypatch):
    gpu = NS(type=4)
    monkeypatch.setitem(sys.modules, "pyopencl", NS(device_type=NS(CPU=2, GPU=4),
                        get_platforms=lambda: [None, NS(get_devices=lambda: [gpu])]))
    def forbidden(*args):
        pytest.fail("A GPU must never be bound into the CPU slot")
    monkeypatch.setitem(sys.modules, "bempp_cl.api", NS(set_default_cpu_device=forbidden))
    with pytest.raises(RuntimeError, match="non-CPU"):
        probe.bind_device(CPU)


@pytest.mark.parametrize('stage', ['inventory', 'smoke'])
@pytest.mark.parametrize('error', [False, True])
def test_transient_failure_is_retried_then_pass_is_cached(monkeypatch, stage, error):
    clock = [0.0]
    monkeypatch.setattr(probe, 'time', NS(monotonic=lambda: clock[0]))
    calls = []
    # A probe error is run again at once, so it must fail both runs here to
    # stay unresolved; a timeout is not run again.
    failing_runs = 2 if error else 1
    def run(mode, device, timeout):
        calls.append(mode)
        if mode == stage and calls.count(stage) <= failing_runs:
            return {'ok': False, 'reason': 'under load',
                    'opencl_unavailable_reason': 'probe_error' if error else (f'{stage}_test_timeout' if stage == 'smoke' else 'inventory_timeout')}
        return {'ok': True, 'devices': [CPU]} if mode == 'inventory' else {'ok': True, 'smoke': {}}
    monkeypatch.setattr(probe, '_run_probe', run)
    assert not probe.qualified_opencl()['ok']
    assert probe.retry_pending()
    assert probe._cached_verdict is None
    assert not probe._device_verdict_cache
    assert not probe.qualified_opencl()['ok']  # throttled
    assert calls == ([stage] * failing_runs if stage == 'inventory' else ['inventory'] + ['smoke'] * failing_runs)
    clock[0] += probe.RETRY_INTERVAL_SECONDS
    assert probe.qualified_opencl()['device'] == CPU
    assert not probe.retry_pending()
    count = len(calls)
    clock[0] += 100
    assert probe.qualified_opencl()['ok']
    assert len(calls) == count


@pytest.mark.parametrize('failure', ['no_device', 'wrong'])
def test_definitive_rejection_is_cached(monkeypatch, failure):
    calls = []
    def run(mode, device, timeout):
        calls.append(mode)
        if mode == 'inventory':
            return {'ok': True, 'devices': [] if failure == 'no_device' else [CPU]}
        return {'ok': False, 'reason': 'wrong result', 'opencl_unavailable_reason': 'smoke_test_failed'}
    monkeypatch.setattr(probe, '_run_probe', run)
    first = probe.qualified_opencl()
    assert first['opencl_unavailable_reason'] == ('no_device' if failure == 'no_device' else 'smoke_test_failed')
    count = len(calls)
    for _ in range(5):
        assert probe.qualified_opencl() == first
    assert len(calls) == count
    assert not probe.retry_pending()


@pytest.mark.parametrize('stage', ['inventory', 'smoke'])
@pytest.mark.parametrize('error', [False, True])
def test_transient_retries_have_interval_and_terminal_cap(monkeypatch, stage, error):
    import asyncio
    from server.diagnostics.capabilities import capabilities_payload
    from server.engines.registry import EngineInfo

    clock = [0.0]
    monkeypatch.setattr(probe, 'time', NS(monotonic=lambda: clock[0]))
    attempts = []
    code = 'probe_error' if error else ('inventory_timeout' if stage == 'inventory' else 'smoke_test_timeout')
    runs = 2 if error else 1  # a probe error is run again at once
    def run(mode, device, timeout):
        if mode == 'inventory' and stage == 'smoke':
            return {'ok': True, 'devices': [CPU]}
        attempts.append(clock[0])
        return {'ok': False, 'reason': 'hung', 'opencl_unavailable_reason': code}
    monkeypatch.setattr(probe, '_run_probe', run)
    for attempt in range(probe.MAX_TIMEOUT_ATTEMPTS):
        assert probe.qualified_opencl()['opencl_unavailable_reason'] == code
        class Snapshot:
            async def capabilities(self):
                return (EngineInfo('bempp', True, 'timeout', None,
                                   assembly_backend='numba', opencl_unavailable_reason=code),)
        payload = asyncio.run(capabilities_payload(Snapshot()))
        assert payload['engines'][0]['opencl_retry_pending'] is (attempt + 1 < probe.MAX_TIMEOUT_ATTEMPTS)
        for _ in range(20):
            assert not probe.qualified_opencl()['ok']
        assert len(attempts) == (attempt + 1) * runs
        clock[0] += probe.RETRY_INTERVAL_SECONDS - 0.01
        assert not probe.qualified_opencl()['ok']
        assert len(attempts) == (attempt + 1) * runs
        clock[0] += 0.01
    clock[0] += 100
    assert probe.qualified_opencl()['opencl_unavailable_reason'] == code
    assert len(attempts) == probe.MAX_TIMEOUT_ATTEMPTS * runs
    assert not probe.retry_pending()


def test_concurrent_requests_run_only_one_qualification(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(mode, device, timeout):
        calls.append(mode)
        entered.set()
        assert release.wait(2)
        return {'ok': False, 'reason': 'hung', 'opencl_unavailable_reason': 'inventory_timeout'}
    monkeypatch.setattr(probe, '_run_probe', run)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(probe.qualified_opencl) for _ in range(6)]
        assert entered.wait(2)
        release.set()
        assert all(f.result(timeout=2)['opencl_unavailable_reason'] == 'inventory_timeout' for f in futures)
    assert calls == ['inventory']


def test_older_status_cannot_pin_numba_when_another_caller_recovers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from server.solver import bempp

    bempp.bempp_status.cache_clear()
    clock, calls = [0.0], []
    monkeypatch.setattr(probe, 'time', NS(monotonic=lambda: clock[0]))
    monkeypatch.setattr(bempp, '_load_api', lambda: True)
    def run(mode, device, timeout):
        if mode == 'inventory':
            calls.append(mode)
            if len(calls) == 1:
                return {'ok': False, 'reason': 'slow', 'opencl_unavailable_reason': 'inventory_timeout'}
            return {'ok': True, 'devices': [CPU]}
        return {'ok': True, 'smoke': {}}
    monkeypatch.setattr(probe, '_run_probe', run)
    entered, release = threading.Event(), threading.Event()
    real_status = bempp._probe_bempp_status
    def delayed_status():
        status = real_status()
        if status['assembly_backend'] == 'numba':
            entered.set()
            assert release.wait(2)
        return status
    monkeypatch.setattr(bempp, '_probe_bempp_status', delayed_status)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(bempp.bempp_status)
            assert entered.wait(2)
            clock[0] += probe.RETRY_INTERVAL_SECONDS
            assert probe.qualified_opencl()['ok']
            release.set()
            assert old.result(timeout=2)['assembly_backend'] == 'numba'
        assert bempp.bempp_status()['assembly_backend'] == 'opencl'
        assert calls == ['inventory', 'inventory']
    finally:
        release.set()
        bempp.bempp_status.cache_clear()


def test_registry_does_not_retry_a_timeout_outside_its_snapshot(monkeypatch):
    import asyncio
    from server.engines.registry import EngineInfo, EngineRegistry

    clock, timers, attempts = [0.0], [], []
    monkeypatch.setattr(probe, "time", NS(monotonic=lambda: clock[0]))
    def timeout(*args):
        attempts.append(args)
        return {"ok": False, "opencl_unavailable_reason": "inventory_timeout", "reason": "slow"}
    monkeypatch.setattr(probe, "_run_probe", timeout)
    assert not probe.qualified_opencl()["ok"]
    clock[0] += probe.RETRY_INTERVAL_SECONDS
    async def timer(_self):
        timers.append(clock[0])
        await asyncio.sleep(0)
    monkeypatch.setattr(EngineRegistry, "_wait_opencl_retry", timer)
    # A custom detector or an unavailable wrapper does not publish this
    # process's timeout. Retrying it would spin once the interval expires.
    other = EngineInfo("bempp", False, "wrapper unavailable", None)
    registry = EngineRegistry(detector=lambda: [other], cpu_refresh=False)
    async def exercise():
        try:
            assert await registry.capabilities() == (other,)
            for _ in range(5):
                await asyncio.sleep(0)
            assert not timers
            assert len(attempts) == 1
        finally:
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


@pytest.mark.parametrize("code", sorted(probe.OPENCL_UNAVAILABLE_REASONS))
@pytest.mark.parametrize("detail", ["check failed", "check failed.", "check failed!", "check failed?", ""])
def test_fallback_sentence_boundaries(monkeypatch, code, detail):
    from server.solver import bempp
    monkeypatch.setattr(bempp, "retry_pending", lambda: True)
    text = bempp.numba_fallback_warning(detail, code)
    assert text.endswith(".")
    assert ".." not in text
    if code == "probe_error":
        assert text == ("WG's OpenCL check could not complete (internal error); "
                        "using the slower numba engine for now and retrying.")
        assert "unusable" not in text
        monkeypatch.setattr(bempp, "retry_pending", lambda: False)
        assert "retrying" not in bempp.numba_fallback_warning(detail, code)
    else:
        assert f"{detail.rstrip('.!?')}. Until" in text


@pytest.mark.parametrize("system", ["win32", "linux", "darwin"])
def test_pocl_probe_error_is_never_a_runtime_rejection(monkeypatch, system):
    monkeypatch.setattr(probe.sys, "platform", system)
    monkeypatch.setattr(probe, "_run_probe", lambda mode, *args:
                        {"ok": True, "devices": [{**CPU, "platform": "PoCL"}]} if mode == "inventory"
                        else {"ok": False, "reason": "internal error", "opencl_unavailable_reason": "probe_error"})
    assert probe.qualified_opencl()["opencl_unavailable_reason"] == "probe_error"
    assert probe.retry_pending()


@pytest.mark.parametrize("output,ready", [
    (probe._READY_MARKER + "\n", True),
    (" " + probe._READY_MARKER + "\n", False),
    ("library " + probe._READY_MARKER + "\n", False),
    (probe._READY_MARKER, False),
    ("x" * 4096 + probe._READY_MARKER + "\n", False),
    ("x" * 5000 + "\n" + probe._READY_MARKER + "\n", True),
])
def test_ready_requires_an_exact_complete_stdout_line(output, ready):
    import io
    import queue
    events = queue.Queue()
    probe._read_probe_output(io.StringIO(output), events)
    lines = []
    while not events.empty():
        lines.append(events.get()[0])
    assert lines == ([probe._READY_MARKER, None] if ready else [None])


@pytest.mark.parametrize("change", [
    {"type": "unknown"}, {"platform_index": "0"}, {"device_index": -1}, {"platform": None},
])
def test_malformed_inventory_is_a_probe_error(monkeypatch, change):
    fake_children(monkeypatch, [child_script({"ok": True, "devices": [{**CPU, **change}]})])
    assert probe._run_probe("inventory", None, 1)["opencl_unavailable_reason"] == "probe_error"


def test_an_internal_qualification_failure_is_recorded_as_an_attempt(monkeypatch):
    """Anything escaping the check is a transient probe error with an attempt
    behind it. Unrecorded, retry_pending() stayed true forever and the
    registry kept BEMPP on "Checking OpenCL…" with every wait raising."""
    clock = [0.0]
    monkeypatch.setattr(probe, "time", NS(monotonic=lambda: clock[0]))
    calls = []
    def broken():
        calls.append(clock[0])
        raise RuntimeError("OpenCL probe reader did not stop")
    monkeypatch.setattr(probe, "_qualified_opencl", broken)
    for attempt in range(probe.MAX_TIMEOUT_ATTEMPTS):
        verdict = probe.qualified_opencl()
        assert verdict["opencl_unavailable_reason"] == "probe_error"
        assert "RuntimeError: OpenCL probe reader did not stop" in verdict["reason"]
        assert probe._timeout_attempts == attempt + 1
        assert probe.retry_pending() is (attempt + 1 < probe.MAX_TIMEOUT_ATTEMPTS)
        assert probe.qualified_opencl() == verdict  # within the interval: no new run
        clock[0] += probe.RETRY_INTERVAL_SECONDS
    assert len(calls) == probe.MAX_TIMEOUT_ATTEMPTS
    assert probe.qualified_opencl()["opencl_unavailable_reason"] == "probe_error"
    assert len(calls) == probe.MAX_TIMEOUT_ATTEMPTS


def test_cancellation_is_still_not_an_attempt(monkeypatch):
    def cancelled():
        raise probe.ProbeCancelled("stopping")
    monkeypatch.setattr(probe, "_qualified_opencl", cancelled)
    with pytest.raises(probe.ProbeCancelled):
        probe.qualified_opencl()
    assert probe._timeout_attempts == 0
    assert not probe.retry_pending()


def _timed_out_bempp_row():
    from server.engines.registry import EngineInfo

    return EngineInfo("bempp", True, "OpenCL smoke compute timed out", None, qualification="done",
                      assembly_backend="numba", opencl_unavailable_reason="smoke_test_timeout")


def test_a_refresh_that_raises_ends_done_and_unavailable(monkeypatch):
    """The 0.3.4 wedge: a retry whose check raised left BEMPP pending, and
    every BEMPP plan or solve re-raised it as an HTTP 500."""
    import asyncio
    from server.engines.registry import EngineRegistry
    from server.solver import bempp

    clock = [0.0]
    monkeypatch.setattr(probe, "time", NS(monotonic=lambda: clock[0]))
    monkeypatch.setattr(probe, "_run_probe", lambda *a: {
        "ok": False, "opencl_unavailable_reason": "smoke_test_timeout", "reason": "slow"})
    assert not probe.qualified_opencl()["ok"]  # the first attempt timed out
    clock[0] += probe.RETRY_INTERVAL_SECONDS  # its retry is due
    statuses = []
    def raising_status():
        statuses.append(clock[0])
        raise RuntimeError("OpenCL probe reader did not stop")
    monkeypatch.setattr(bempp, "bempp_status", raising_status)
    registry = EngineRegistry(detector=lambda: [_timed_out_bempp_row()], cpu_refresh=False)
    async def exercise():
        try:
            await registry.capabilities()
            capabilities = await registry.wait_for_bempp()  # must not raise
            row = next(item for item in capabilities if item.name == "bempp")
            assert row.qualification == "done"
            assert not row.available
            assert row.opencl_unavailable_reason == "probe_error"
            assert "OpenCL probe reader did not stop" in row.reason
            # The failure is an attempt: the next retry waits for its interval.
            assert probe._timeout_attempts == 2
            assert not probe.retry_due()
            for _ in range(20):
                assert await registry.get_engine("bempp") is None
                assert "OpenCL probe reader did not stop" in await registry.unavailable_reason("bempp")
                await asyncio.sleep(0)
            assert len(statuses) == 1, statuses
            # The last allowed retry fails the same way, and the cap ends it.
            clock[0] += probe.RETRY_INTERVAL_SECONDS
            row = next(item for item in await registry.wait_for_bempp() if item.name == "bempp")
            assert (row.qualification, row.available) == ("done", False)
            assert len(statuses) == 2
            assert not probe.retry_pending()
            clock[0] += 100
            for _ in range(5):
                await registry.wait_for_bempp()
            assert len(statuses) == 2
        finally:
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


def test_a_bempp_detection_that_raises_is_published_as_unavailable(monkeypatch):
    import asyncio
    from server.engines import registry as reg

    def detector(*, names=None, environ=None):
        if "bempp" in names:
            raise RuntimeError("OpenCL probe reader did not stop")
        return [reg.EngineInfo(name, False, "absent here", None) for name in names]
    monkeypatch.setattr(reg, "detect_engines", detector)
    registry = reg.EngineRegistry(detector=detector, cpu_refresh=False)
    async def exercise():
        try:
            await registry.capabilities()
            capabilities = await registry.wait_for_bempp()
            row = next(item for item in capabilities if item.name == "bempp")
            assert (row.qualification, row.available, row.opencl_unavailable_reason) == (
                "done", False, "probe_error")
            assert row.reason == "bempp detection failed: OpenCL probe reader did not stop"
            assert await registry.get_engine("bempp") is None
            assert all(item.qualification != "pending" for item in await registry.capabilities())
        finally:
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


def test_a_refresh_stopped_by_shutdown_still_propagates(monkeypatch):
    import asyncio
    from server.engines.registry import EngineRegistry
    from server.solver import bempp

    def cancelled():
        raise probe.ProbeCancelled("stopping")
    monkeypatch.setattr(bempp, "bempp_status", cancelled)
    registry = EngineRegistry(detector=lambda: [_timed_out_bempp_row()], cpu_refresh=False)
    async def exercise():
        try:
            await registry.capabilities()
            with pytest.raises(probe.ProbeCancelled):
                await registry._refresh_bempp_timeout()
        finally:
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


def test_recording_a_failed_refresh_never_blocks_the_event_loop(monkeypatch):
    """The attempt lock is held through a whole running check, minutes at
    worst; taking it on the event loop would freeze every request."""
    import asyncio
    import threading
    from server.engines.registry import EngineRegistry
    from server.solver import bempp

    def raising_status():
        raise RuntimeError("OpenCL probe reader did not stop")
    monkeypatch.setattr(bempp, "bempp_status", raising_status)
    registry = EngineRegistry(detector=lambda: [_timed_out_bempp_row()], cpu_refresh=False)
    held, release = threading.Event(), threading.Event()
    def hold():
        with probe._selection_lock:
            held.set()
            release.wait(5)
    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(2)
    async def exercise():
        ticks = 0
        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)
        beat = asyncio.create_task(heartbeat())
        try:
            await registry.capabilities()
            refresh = asyncio.create_task(registry._refresh_bempp_timeout())
            await asyncio.sleep(0.3)
            assert not refresh.done()
            assert ticks > 5, "the event loop waited on the attempt lock"
            release.set()
            await asyncio.wait_for(refresh, 5)
            row = next(item for item in registry._cache if item.name == "bempp")
            assert (row.qualification, row.available, row.opencl_unavailable_reason) == (
                "done", False, "probe_error")
            assert probe._timeout_attempts == 1
        finally:
            release.set()
            beat.cancel()
            await registry.shutdown_prewarm()
    asyncio.run(exercise())
    holder.join(2)


def test_initial_detection_never_leaves_opencl_retry_pending_behind(monkeypatch):
    """Production detect_engines: a check that records a timeout, then status
    processing that raises. That used to publish a bare "detection failed"
    row with no retry while opencl_retry_pending stayed true, which the
    interface shows as "Checking OpenCL…" forever."""
    import asyncio
    from server.diagnostics.capabilities import capabilities_payload
    from server.engines import registry as reg
    from server.solver import bempp

    bempp.bempp_status.cache_clear()
    clock = [0.0]
    monkeypatch.setattr(probe, "time", NS(monotonic=lambda: clock[0]))
    monkeypatch.setattr(probe, "_run_probe", lambda mode, device, timeout: (
        {"ok": True, "devices": [CPU]} if mode == "inventory"
        else {"ok": False, "opencl_unavailable_reason": "smoke_test_timeout", "reason": "slow"}))
    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    def broken_axes():
        raise RuntimeError("status processing failed")
    monkeypatch.setattr(bempp, "_probe_ground_plane_axes", broken_axes)
    real_detect = reg.detect_engines
    def detector(*, names=None, environ=None):
        if "bempp" in names:
            return real_detect(names=names)
        return [reg.EngineInfo(name, False, "absent here", None) for name in names]
    monkeypatch.setattr(reg, "detect_engines", detector)
    async def due(_self):
        clock[0] = max(clock[0], probe._retry_after)
        await asyncio.sleep(0)
    monkeypatch.setattr(reg.EngineRegistry, "_wait_opencl_retry", due)
    registry = reg.EngineRegistry(detector=detector, cpu_refresh=False)
    async def exercise():
        try:
            await registry.capabilities()
            await registry.wait_for_bempp()
            for _ in range(500):
                payload = await capabilities_payload(registry)
                row = next(item for item in payload["engines"] if item["name"] == "bempp")
                if row["qualification"] == "done" and row["opencl_retry_pending"] is False:
                    break
                await asyncio.sleep(0.01)
            assert row["qualification"] == "done", row
            assert row["opencl_retry_pending"] is False, row
            assert not row["available"]
            assert "status check failed" in row["reason"]
            assert probe._timeout_attempts == probe.MAX_TIMEOUT_ATTEMPTS
        finally:
            await registry.shutdown_prewarm()
    try:
        asyncio.run(exercise())
    finally:
        bempp.bempp_status.cache_clear()


def test_detection_lets_shutdown_propagate(monkeypatch):
    from server.engines import registry as reg
    from server.solver import bempp

    def cancelled():
        raise probe.ProbeCancelled("stopping")
    monkeypatch.setattr(bempp, "bempp_status", cancelled)
    with pytest.raises(probe.ProbeCancelled):
        reg.detect_engines(names=("bempp",))


def test_a_failure_does_not_double_count_a_concurrent_attempt(monkeypatch):
    """Decided under the attempt lock: a verdict recorded after the caller's
    snapshot means its raise stood in for nothing, and is not counted."""
    clock = [0.0]
    monkeypatch.setattr(probe, "time", NS(monotonic=lambda: clock[0]))
    monkeypatch.setattr(probe, "_run_probe", lambda *a: {
        "ok": False, "opencl_unavailable_reason": "inventory_timeout", "reason": "slow"})
    snapshot = probe.qualification_revision()
    probe.qualified_opencl()  # another caller's attempt lands first
    assert not probe.record_failed_attempt(RuntimeError("late"), snapshot)
    assert probe._timeout_attempts == 1
    assert probe._last_timeout["opencl_unavailable_reason"] == "inventory_timeout"
    # Within the interval nothing was skipped, so nothing is counted either.
    assert not probe.record_failed_attempt(RuntimeError("again"), probe.qualification_revision())
    clock[0] += probe.RETRY_INTERVAL_SECONDS
    assert probe.record_failed_attempt(RuntimeError("due"), probe.qualification_revision())
    assert probe._timeout_attempts == 2
    assert "BEMPP status check" in probe._last_timeout["reason"]
    assert "OpenCL check" not in probe._last_timeout["reason"]


def test_a_raise_answers_from_a_newer_verdict(monkeypatch):
    """A good verdict another caller recorded is published, not shadowed."""
    from server.engines import registry as reg

    calls = []
    def status():
        calls.append(1)
        if len(calls) == 1:
            probe._record_verdict({"ok": True, "opencl_unavailable_reason": None, "reason": "fine"})
            raise RuntimeError("raced")
        return {"available": True, "reason": "OpenCL CPU passed", "assembly_backend": "opencl"}
    published, revision = reg.bempp_status_or_failure(status)
    assert published["available"] and published["assembly_backend"] == "opencl"
    assert revision == probe.qualification_revision()
    assert probe._timeout_attempts == 0


def test_a_failure_row_keeps_nothing_the_previous_row_declared():
    from server.engines.registry import EngineInfo, _failed_detection

    before = EngineInfo(
        "bempp", True, "OpenCL passed", "1.0", label="BEMPP — CPU", qualification="done",
        formulations=("full-3d",), mountings=("free-standing", "ground-plane"),
        ground_plane_axes=("y",), ground_plane_composes_with_symmetry=True,
        geometry_sources=("parametric", "imported"), imported_features=("x",),
        symmetry_domains=("full", "half"), field_traces=True, di_sphere=True,
        cancellation_granularity="intra-frequency", assembly_backend="opencl",
        assembly_device={"type": "cpu"},
    )
    after = _failed_detection(before, RuntimeError("boom"))
    assert (after.name, after.label, after.available, after.version) == ("bempp", "BEMPP — CPU", False, None)
    assert after.reason == "bempp detection failed: boom"
    assert (after.qualification, after.opencl_unavailable_reason) == ("done", "probe_error")
    assert after.assembly_backend is None and after.assembly_device is None
    assert after.formulations == after.mountings == after.ground_plane_axes == ()
    assert after.geometry_sources == after.imported_features == after.symmetry_domains == ()
    assert not after.ground_plane_composes_with_symmetry
    assert not after.field_traces and not after.di_sphere
    other = _failed_detection(EngineInfo("metal", True, "ok", "1", mountings=("free-standing",)),
                              RuntimeError("x"))
    assert other.mountings == () and other.qualification is None
    assert other.opencl_unavailable_reason is None


def test_an_embedder_detector_that_raises_is_never_pending(monkeypatch):
    import asyncio
    from server.diagnostics.capabilities import capabilities_payload
    from server.engines.registry import EngineRegistry

    def broken():
        raise RuntimeError("embedder detector failed")
    registry = EngineRegistry(detector=broken, cpu_refresh=False)
    async def exercise():
        try:
            capabilities = await registry.capabilities()
            assert capabilities and not any(item.available for item in capabilities)
            assert all(item.qualification != "pending" for item in capabilities)
            row = next(item for item in await registry.wait_for_bempp() if item.name == "bempp")
            assert (row.qualification, row.opencl_unavailable_reason) == ("done", "probe_error")
            assert await registry.get_engine("bempp") is None
            payload = await capabilities_payload(registry)
            bempp_row = next(item for item in payload["engines"] if item["name"] == "bempp")
            assert bempp_row["opencl_retry_pending"] is False
        finally:
            await registry.shutdown_prewarm()
    asyncio.run(exercise())


def test_an_embedder_detector_lets_shutdown_propagate():
    import asyncio
    from server.engines.registry import EngineRegistry

    def stopping():
        raise probe.ProbeCancelled("stopping")
    registry = EngineRegistry(detector=stopping, cpu_refresh=False)
    with pytest.raises(probe.ProbeCancelled):
        asyncio.run(registry._detect_initial())
