"""An enumerable ICD is not evidence that a BEMPP kernel computed correctly."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from server.solver import bempp_opencl as probe


CPU = {"platform_index": 1, "device_index": 0, "type": "cpu", "platform": "PoCL", "vendor": "CPU", "name": "CPU", "fp64": True}
GPU = {"platform_index": 0, "device_index": 0, "type": "gpu", "platform": "Apple", "vendor": "Apple", "name": "M1 GPU", "fp64": False}


@pytest.fixture(autouse=True)
def clear_cache():
    probe.clear_cache()
    yield
    probe.clear_cache()


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
        def __iter__(self):
            for delay, line in self.script:
                clock[0] += delay
                yield line + "\n"
        def close(self):
            self.closed = True
    class Child:
        def __init__(self, argv, **kwargs):
            assert kwargs["stderr"] == subprocess.STDOUT
            assert kwargs["stdout"] == subprocess.PIPE
            assert kwargs["env"]["NUMBA_DISABLE_JIT"] == "1"
            self.args, self.returncode = argv, None
            self.stdout = Stream(next(scripts))
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
    monkeypatch.setattr(probe.queue, "Queue", Events)
    # Do not patch threading globally: endpoint tests still use real workers.
    monkeypatch.setattr(probe, "threading", NS(Thread=Reader, Lock=__import__("threading").Lock))
    return clock, children


def child_script(verdict, *, import_seconds=0.0, compute_seconds=0.0):
    return [(import_seconds, probe._READY_MARKER),
            (compute_seconds, probe._RESULT_PREFIX + json.dumps(verdict))]


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


@pytest.mark.parametrize("output", ["not json", "WG_OPENCL_RESULT invalid", "WG_OPENCL_RESULT []"])
def test_crashed_or_malformed_probe_is_rejected(monkeypatch, output):
    fake_children(monkeypatch, [[(0, probe._READY_MARKER), (0, output)]])
    assert not probe._run_probe("smoke", CPU, 1)["ok"]


def test_probe_drains_large_stderr_and_ignores_import_logs(monkeypatch):
    # Real pipe/reader coverage complements virtual timing; no near-limit sleep.
    real_popen = subprocess.Popen
    def child(argv, **kwargs):
        script = ("import sys; sys.stderr.write('log' * 100000); "
                  f"print('\\n{probe._READY_MARKER}', flush=True); "
                  f"print({probe._RESULT_PREFIX + json.dumps({'ok': True})!r}, flush=True)")
        return real_popen([sys.executable, "-c", script], **kwargs)
    monkeypatch.setattr(probe.subprocess, "Popen", child)
    assert probe._run_probe("smoke", CPU, 20)["ok"]


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


def test_pocl_zero_compute_is_rejected_and_gpu_is_never_smoke_tested(monkeypatch):
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


@pytest.mark.parametrize("system,code", [("win32", "pocl_windows"), ("linux", "smoke_test_failed")])
def test_pocl_windows_zero_compute_reason(monkeypatch, system, code):
    monkeypatch.setattr(probe.sys, "platform", system)
    monkeypatch.setattr(probe, "_run_probe", lambda mode, *args:
                        {"ok": True, "devices": [CPU]} if mode == "inventory"
                        else {"ok": False, "reason": "zero computation"})
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
def test_timeout_is_retried_then_pass_is_cached(monkeypatch, stage):
    clock = [0.0]
    monkeypatch.setattr(probe, 'time', NS(monotonic=lambda: clock[0]))
    calls = []
    def run(mode, device, timeout):
        calls.append(mode)
        if mode == stage and calls.count(stage) == 1:
            return {'ok': False, 'reason': 'under load',
                    'opencl_unavailable_reason': f'{stage}_test_timeout' if stage == 'smoke' else 'inventory_timeout'}
        return {'ok': True, 'devices': [CPU]} if mode == 'inventory' else {'ok': True, 'smoke': {}}
    monkeypatch.setattr(probe, '_run_probe', run)
    assert not probe.qualified_opencl()['ok']
    assert probe.retry_pending()
    assert probe._cached_verdict is None
    assert not probe._device_verdict_cache
    assert not probe.qualified_opencl()['ok']  # throttled
    assert calls == ([stage] if stage == 'inventory' else ['inventory', 'smoke'])
    clock[0] += probe.RETRY_INTERVAL_SECONDS
    assert probe.qualified_opencl()['device'] == CPU
    assert not probe.retry_pending()
    count = len(calls)
    clock[0] += 100
    assert probe.qualified_opencl()['ok']
    assert len(calls) == count


@pytest.mark.parametrize('failure', ['no_device', 'wrong', 'crash'])
def test_definitive_rejection_is_cached(monkeypatch, failure):
    calls = []
    def run(mode, device, timeout):
        calls.append(mode)
        if mode == 'inventory':
            return {'ok': True, 'devices': [] if failure == 'no_device' else [CPU]}
        return {'ok': False, 'reason': 'wrong result', 'opencl_unavailable_reason': 'smoke_test_failed'}
    if failure == 'crash':
        fake_children(monkeypatch, [child_script({'ok': True, 'devices': [CPU]}),
                                   [(0, probe._READY_MARKER), (0, 'crashed')]])
    else:
        monkeypatch.setattr(probe, '_run_probe', run)
    first = probe.qualified_opencl()
    assert first['opencl_unavailable_reason'] == ('no_device' if failure == 'no_device' else 'smoke_test_failed')
    count = len(calls)
    for _ in range(5):
        assert probe.qualified_opencl() == first
    assert len(calls) == count
    assert not probe.retry_pending()


@pytest.mark.parametrize('stage', ['inventory', 'smoke'])
def test_timeout_retries_have_interval_and_terminal_cap(monkeypatch, stage):
    import asyncio
    from server.diagnostics.capabilities import capabilities_payload
    from server.engines.registry import EngineInfo

    clock = [0.0]
    monkeypatch.setattr(probe, 'time', NS(monotonic=lambda: clock[0]))
    attempts = []
    code = 'inventory_timeout' if stage == 'inventory' else 'smoke_test_timeout'
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
        assert len(attempts) == attempt + 1
        clock[0] += probe.RETRY_INTERVAL_SECONDS - 0.01
        assert not probe.qualified_opencl()['ok']
        assert len(attempts) == attempt + 1
        clock[0] += 0.01
    clock[0] += 100
    assert probe.qualified_opencl()['opencl_unavailable_reason'] == code
    assert len(attempts) == probe.MAX_TIMEOUT_ATTEMPTS
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
