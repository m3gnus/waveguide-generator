"""Every BEMPP consumer must retain WG's compute-qualified CPU/numba route."""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace as NS

import numpy as np
import pytest

from server.solver import bempp, bempp_opencl as probe, bempp_process, field_plane, warmup
from server.solver.field_traces_store import BEMPP_FIELD_TRACE_BACKEND
from server.tests.test_engines_adapters import _cabinet_msh, _context, _result
from server.tests import test_imported_bempp as imported


@pytest.fixture
def host(monkeypatch):
    """Actual installed default selection/binding on the review's mixed platform."""
    import pyopencl as cl
    import bempp_cl.api as api
    from bempp_cl.core import opencl_kernels as kernels
    from hornlab_bempp_bem import device as native_device

    bempp.bempp_status.cache_clear()
    monkeypatch.setattr(field_plane, "_BEMPP_MESH_CACHE", field_plane.OrderedDict())
    platform = NS(name="mixed platform")
    def device(name, kind):
        return NS(name=name, type=kind, platform=platform, vendor="vendor",
                  double_fp_config=1, extensions="cl_khr_fp64",
                  native_vector_width_float=4, native_vector_width_double=2)
    cpu, gpu = device("CPU", 2), device("GPU", 4)
    devices, contexts, bindings = [cpu, gpu], [], []
    platform.get_devices = lambda: devices
    def context(*, devices=None, **_kwargs):
        selected = list(devices) if devices is not None else [cpu, gpu]
        contexts.append(selected)
        return NS(devices=selected)
    monkeypatch.setattr(cl, "get_platforms", lambda: [platform])
    monkeypatch.setattr(cl, "Context", context)
    for name in ("_DEFAULT_CPU_DEVICE", "_DEFAULT_CPU_CONTEXT", "_DEFAULT_GPU_DEVICE", "_DEFAULT_GPU_CONTEXT"):
        monkeypatch.setattr(kernels, name, None)
    for name in ("_active_device_type", "_active_device_name"):
        monkeypatch.setattr(native_device, name, None)
    for name in ("BOUNDARY_OPERATOR_DEVICE_TYPE", "POTENTIAL_OPERATOR_DEVICE_TYPE"):
        monkeypatch.setattr(api, name, "cpu")
    set_cpu = api.set_default_cpu_device
    def bind(pi, di):
        bindings.append(devices[di])
        assert devices[di] is cpu, "GPU reached BEMPP's default CPU slot"
        set_cpu(pi, di)
    monkeypatch.setattr(api, "set_default_cpu_device", bind)
    monkeypatch.setattr(api, "set_default_gpu_device", lambda *_args: pytest.fail("GPU binding"))
    monkeypatch.setenv("PYOPENCL_CTX", "0:1")
    monkeypatch.setenv("BEMPP_CPU_DRIVER", "mixed platform")
    yield NS(cpu=cpu, gpu=gpu, devices=devices, contexts=contexts, bindings=bindings, kernels=kernels)
    native_device.reset_opencl_device()
    bempp.bempp_status.cache_clear()


def qualify(monkeypatch, host, state):
    if state == "gpu_only":
        host.devices[:] = [host.gpu]
    def run(mode, device, _timeout):
        if mode == "inventory":
            return {"ok": True, "devices": probe.inventory()}
        assert device["type"] == "cpu"
        return ({"ok": True, "smoke": {}} if state == "qualified_cpu" else
                {"ok": False, "reason": "CPU smoke computed zeros", "opencl_unavailable_reason": "smoke_test_failed"})
    monkeypatch.setattr(probe, "_run_probe", run)
    return "opencl" if state == "qualified_cpu" else "numba"


@pytest.mark.parametrize("state", ["qualified_cpu", "failed_cpu_smoke", "gpu_only"])
def test_every_native_consumer_preserves_qualification(monkeypatch, host, state):
    """Parametric, imported, warmup, potential/observation and retained field."""
    import hornlab_bempp_bem as package
    from hornlab_bempp_bem import field_traces as traces, bie
    import bempp_cl.api as api

    backend = qualify(monkeypatch, host, state)
    observed = []
    def record(config):
        assert config.assembly_backend == backend
        assert config.opencl_device == "cpu"
        # This installed helper feeds both boundary assembly and observations.
        kwargs = bie._operator_kwargs(config.assembly_backend, config.precision, config.opencl_device)
        assert kwargs["device_interface"] == backend
        if backend == "opencl":
            assert host.kernels.default_cpu_device() is host.cpu
            assert host.kernels.default_device() is host.cpu  # singular assembly
            assert host.kernels.default_context().devices == [host.cpu]
        observed.append(backend)
    def solve(_path, config):
        record(config)
        return _result()
    monkeypatch.setattr(bempp, "bempp_solve", solve)
    monkeypatch.setattr(package, "solve", solve)
    bempp.solve_bempp_from_msh_text(_cabinet_msh(), _context(field_plane=False))
    warmup.warm_bempp_in_this_process(bempp.bempp_status())
    recorder = imported._RecordingBempp()
    def sweep(path, frequencies, config):
        record(config)
        return recorder.solve_frequencies(path, frequencies, config)
    monkeypatch.setattr(bempp, "bempp_solve_frequencies", sweep)
    if backend == "opencl":
        imported._solve(imported._request(), imported._record(planes=["x0"]))
    else:
        with pytest.raises(bempp.BemppUnavailable, match="OpenCL device only"):
            imported._solve(imported._request(), imported._record(planes=["x0"]))

    # Review reproduction: retain the actual evaluator, resolver, configure
    # and default functions; replace only geometry and final kernel execution.
    assert inspect.signature(package.evaluate_exterior_from_traces).parameters["assembly_backend"].default == "auto"
    monkeypatch.setattr(package, "load_mesh", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(traces, "_trace_grid", lambda _: (object(), None))
    monkeypatch.setattr(traces, "_setup_function_spaces", lambda _: (NS(global_dof_count=1), NS(global_dof_count=1)))
    monkeypatch.setattr(api, "GridFunction", lambda *_args, **kwargs: NS(**kwargs))
    def potential(*args):
        assert args[6]["device_interface"] == backend
        if backend == "opencl":
            assert host.kernels.default_cpu_device() is host.cpu
        observed.append(backend)
        return np.ones(args[5].shape[0], dtype=np.complex128)
    monkeypatch.setattr(traces, "_evaluate_far_field", potential)
    result = bempp_process._solve_payload({
        "kind": "field", "traces": ("mesh", 1000., 18., None, np.ones(1), np.ones(1), BEMPP_FIELD_TRACE_BACKEND, "revision"),
        "points": np.array([[1., 0., 0.]]),
    }, stage=lambda *_args: None, result=lambda *_args: None)
    np.testing.assert_array_equal(result.pressure, [1])
    assert observed == [backend] * (5 if backend == "opencl" else 3)
    if backend == "numba":
        assert host.bindings == []
        assert host.contexts == []
    else:
        assert host.bindings and all(device is host.cpu for device in host.bindings)
        assert all(context == [host.cpu] for context in host.contexts)


@pytest.mark.parametrize("state", ["qualified_cpu", "failed_cpu_smoke", "gpu_only"])
@pytest.mark.parametrize("backend,device", [(None, None), ("auto", "cpu"), ("opencl", "gpu"), ("numba", "gpu")])
def test_lowest_native_boundary_refuses_implicit_gpu_and_unqualified(monkeypatch, host, state, backend, device):
    qualify(monkeypatch, host, state)
    with pytest.raises(RuntimeError, match="refuses"):
        probe.native_call(lambda **_kwargs: pytest.fail("unsafe native call ran"),
                          assembly_backend=backend, opencl_device=device)
    assert host.bindings == []
    assert host.contexts == []


@pytest.mark.parametrize("state", ["failed_cpu_smoke", "gpu_only"])
def test_explicit_cpu_opencl_cannot_bypass_rejection(monkeypatch, host, state):
    qualify(monkeypatch, host, state)
    with pytest.raises(RuntimeError, match="unqualified"):
        probe.native_call(lambda **_kwargs: pytest.fail("unqualified call ran"),
                          assembly_backend="opencl", opencl_device="cpu")


def test_field_service_sends_bempp_work_across_process_boundary(monkeypatch, tmp_path):
    from server.tests.test_field_plane import _create_job, _body
    from server.jobs.models import FieldPlaneRequest
    from server.jobs.store import JobStore
    from server.solver.metal_permit import MetalPermit

    store = JobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    _create_job(store, "isolated", backend=BEMPP_FIELD_TRACE_BACKEND)
    service = field_plane.FieldPlaneService(store, MetalPermit())
    calls = []
    async def dispatch(payload):
        calls.append(payload)
        return field_plane.FieldPlaneEvaluation(1000., np.ones(6, dtype=np.complex64), "geometry", "revision", None)
    monkeypatch.setattr(bempp_process, "evaluate_field_bempp_in_process", dispatch)
    monkeypatch.setattr(field_plane, "_require_field_backend", lambda _backend: None)
    monkeypatch.setattr(service, "_evaluate_sync", lambda *_args: pytest.fail("native field ran in server thread"))
    result = asyncio.run(service.evaluate("isolated", FieldPlaneRequest.model_validate(_body("isolated"))))
    assert result.pressure.shape == (6,)
    assert calls[0]["traces"][6] == BEMPP_FIELD_TRACE_BACKEND


def test_field_timeout_kills_native_worker(monkeypatch):
    from server.tests.test_bempp_process import _blocking_worker

    async def exercise():
        worker = bempp_process.BemppProcessHost(target=_blocking_worker)
        monkeypatch.setattr(bempp_process, "_HOST", worker)
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(bempp_process.evaluate_field_bempp_in_process({}), 0.2)
            assert worker._process is None
            assert worker._active_job_id is None
        finally:
            worker.close()
    asyncio.run(exercise())


def _crashing_field_worker(connection):
    import os

    connection.recv()
    os._exit(31)


def test_field_native_crash_is_contained(monkeypatch):
    async def exercise():
        worker = bempp_process.BemppProcessHost(target=_crashing_field_worker)
        monkeypatch.setattr(bempp_process, "_HOST", worker)
        monkeypatch.setattr(worker, "prewarm", lambda: None)
        try:
            with pytest.raises(bempp_process.BemppWorkerError, match="exited|disconnected"):
                await bempp_process.evaluate_field_bempp_in_process({})
            assert worker._process is None
        finally:
            worker.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("entry", ["execution", "smoke"])
def test_guard_rechecks_device_even_after_a_cached_bind(monkeypatch, host, entry):
    qualify(monkeypatch, host, "qualified_cpu")
    _backend, device = probe.execution_route()
    probe.bind_device(device)
    host.devices[0] = host.gpu
    with pytest.raises(RuntimeError, match="non-CPU"):
        if entry == "smoke":
            probe.smoke_test(device)
        else:
            probe.native_call(lambda **_kwargs: pytest.fail("stale GPU device reached native code"),
                              assembly_backend="opencl", opencl_device="cpu")


def test_guard_reuses_only_a_matching_one_cpu_context(monkeypatch, host):
    qualify(monkeypatch, host, "qualified_cpu")
    def execute(**_kwargs):
        assert host.kernels.default_cpu_device() is host.cpu
    for _ in range(2):
        probe.native_call(execute, assembly_backend="opencl", opencl_device="cpu")
    assert host.bindings == [host.cpu]
    assert host.contexts == [[host.cpu]]
    # Even the right device in a mixed context must be rebound to a CPU alone.
    host.kernels._DEFAULT_CPU_CONTEXT = NS(devices=[host.cpu, host.gpu])
    probe.native_call(execute, assembly_backend="opencl", opencl_device="cpu")
    assert host.bindings == [host.cpu, host.cpu]
    assert host.contexts == [[host.cpu], [host.cpu]]


def test_later_infinite_baffle_solve_requalifies_cpu_without_restart(monkeypatch, host):
    """Keep the native config/binding boundary, substitute only kernel work."""
    clock, inventories, observed = [0.0], [], []
    monkeypatch.setattr(probe, "time", NS(monotonic=lambda: clock[0]))
    def run(mode, device, timeout):
        if mode == "inventory":
            inventories.append(mode)
            if len(inventories) == 1:
                return {"ok": False, "reason": "startup contention",
                        "opencl_unavailable_reason": "inventory_timeout"}
            return {"ok": True, "devices": probe.inventory()}
        return {"ok": True, "smoke": {}}
    monkeypatch.setattr(probe, "_run_probe", run)
    assert bempp.bempp_status()["assembly_backend"] == "numba"
    clock[0] += probe.RETRY_INTERVAL_SECONDS
    def solve(path, config):
        observed.append(config)
        assert config.assembly_backend == "opencl"
        assert config.opencl_device == "cpu"
        assert host.kernels.default_cpu_device() is host.cpu
        assert config.aperture_tag is not None
        return _result()
    monkeypatch.setattr(bempp, "bempp_solve", solve)
    context = _context(field_plane=False, sim_type=1)
    bempp.solve_bempp_from_msh_text(_cabinet_msh(aperture=True), context, mesh_metadata={"apertureTag": 12})
    assert len(observed) == 1
    assert len(inventories) == 2
    assert bempp.bempp_status()["assembly_backend"] == "opencl"
