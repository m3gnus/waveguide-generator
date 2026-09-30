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
    times = iter([0.0, probe.TOTAL_SECONDS - 1, probe.TOTAL_SECONDS + 1, probe.TOTAL_SECONDS + 1])
    monkeypatch.setattr(probe, "time", NS(monotonic=lambda: next(times)))
    calls = []
    def run(mode, device, timeout):
        calls.append((mode, timeout))
        return {"ok": True, "devices": [CPU, {**CPU, "device_index": 1}]} if mode == "inventory" else {"ok": False, "reason": "timeout"}
    monkeypatch.setattr(probe, "_run_probe", run)
    assert not probe.qualified_opencl()["ok"]
    assert calls == [("inventory", probe.INVENTORY_SECONDS), ("smoke", 1.0)]


def test_hung_icd_subprocess_is_time_bounded(monkeypatch):
    def hang(argv, **kwargs):
        assert kwargs["timeout"] == 0.25
        assert kwargs["env"]["NUMBA_DISABLE_JIT"] == "1"
        assert argv[2] == "smoke"
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(probe.subprocess, "run", hang)
    verdict = probe._run_probe("smoke", CPU, 0.25)
    assert verdict["ok"] is False
    assert "timed out" in verdict["reason"]


@pytest.mark.parametrize("output", ["not json", "WG_OPENCL_RESULT invalid"])
def test_crashed_or_malformed_probe_is_rejected(monkeypatch, output):
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: NS(stdout=output))
    assert not probe._run_probe("smoke", CPU, 1)["ok"]


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


@pytest.mark.parametrize('mode,code', [('inventory', 'inventory_timeout'), ('smoke', 'smoke_test_timeout')])
def test_subprocess_timeout_reason_distinguishes_stage(monkeypatch, mode, code):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
    monkeypatch.setattr(probe.subprocess, 'run', hang)
    assert probe._run_probe(mode, CPU, 0.1)['opencl_unavailable_reason'] == code
    assert code in probe.OPENCL_UNAVAILABLE_REASONS


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
        if failure == 'crash':
            raise subprocess.CalledProcessError(1, 'probe')
        return {'ok': False, 'reason': 'wrong result', 'opencl_unavailable_reason': 'smoke_test_failed'}
    if failure == 'crash':
        # Exercise actual subprocess error handling, not a fabricated verdict.
        def child(argv, **kwargs):
            if argv[2] == 'inventory':
                return NS(stdout=probe._RESULT_PREFIX + json.dumps({'ok': True, 'devices': [CPU]}))
            calls.append('smoke')
            raise subprocess.CalledProcessError(1, argv)
        monkeypatch.setattr(probe.subprocess, 'run', child)
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


def test_real_slow_inventory_then_fast_updates_capability_and_status(monkeypatch):
    import asyncio
    import threading
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.solver import bempp
    from server.diagnostics.capabilities import capabilities_payload

    bempp.bempp_status.cache_clear()
    clock = [0.0]
    monkeypatch.setattr(probe, 'time', NS(monotonic=lambda: clock[0]))
    monkeypatch.setattr(probe, 'INVENTORY_SECONDS', 0.1)
    monkeypatch.setattr(bempp, '_load_api', lambda: True)
    real_run = subprocess.run
    inventory_calls = []
    started = threading.Event()
    def child(argv, **kwargs):
        assert threading.current_thread() is not threading.main_thread()
        mode = argv[2]
        if mode == 'inventory':
            inventory_calls.append(mode)
            started.set()
            delay = 1.0 if len(inventory_calls) == 1 else 0
            verdict = {'ok': True, 'devices': [CPU]}
        else:
            delay, verdict = 0, {'ok': True, 'smoke': {}}
        script = f'import time; time.sleep({delay}); print({probe._RESULT_PREFIX + json.dumps(verdict)!r})'
        return real_run([sys.executable, '-c', script], **kwargs)
    monkeypatch.setattr(probe.subprocess, 'run', child)
    other = EngineInfo('metal', False, 'absent', None)
    def detect():
        status = bempp.bempp_status()
        return [other, EngineInfo('bempp', status['available'], status['reason'], None,
                                 assembly_backend=status['assembly_backend'],
                                 opencl_unavailable_reason=status['opencl_unavailable_reason'],
                                 geometry_sources=bempp.geometry_sources_for(status))]
    registry = EngineRegistry(detector=detect, cpu_refresh=False)
    async def exercise():
        await registry.prewarm()
        assert not registry.warmup.task.done()  # startup only schedules
        while not started.is_set():
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.01)
        assert not registry.warmup.task.done()  # loop runs while child is slow
        initial = await capabilities_payload(registry)
        row = next(e for e in initial['engines'] if e['name'] == 'bempp')
        assert row['assembly_backend'] == 'numba'
        assert row['opencl_unavailable_reason'] == 'inventory_timeout'
        assert tuple(row['geometry_sources']) == ('parametric',)
        monkeypatch.setattr(probe, 'INVENTORY_SECONDS', 1.0)
        clock[0] += probe.RETRY_INTERVAL_SECONDS
        recovered = await capabilities_payload(registry)
        row = next(e for e in recovered['engines'] if e['name'] == 'bempp')
        assert row['assembly_backend'] == 'opencl'
        assert row['assembly_device'] == CPU
        assert row['opencl_unavailable_reason'] is None
        assert 'imported' in row['geometry_sources']
        assert (await registry.capabilities())[0] is other
        assert bempp.bempp_status()['assembly_backend'] == 'opencl'
        await registry.shutdown_prewarm()
    try:
        asyncio.run(exercise())
        assert len(inventory_calls) == 2
    finally:
        bempp.bempp_status.cache_clear()


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
