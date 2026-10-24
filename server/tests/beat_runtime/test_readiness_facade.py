from __future__ import annotations

import asyncio
import threading

import pytest

from server.engines.registry import EngineInfo, EngineRegistry, _official_runtime_statuses
from server.solver import beat_cpu_runtime as facade
from server.solver.beat_runtime import hardware, provider, readiness


@pytest.fixture
def official(monkeypatch, tmp_path):
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs: {name: {"available": name == "metal", "reason": "mock hardware"} for name in hardware.GPU_BACKENDS})
    monkeypatch.setenv(provider.PROVIDER_ENV, "official")
    for name, value in (("_provision_thread", None), ("_provision_step", None),
                        ("_requested_backend", None), ("_preparation_errors", {}),
                        ("_preparation_in_flight", False), ("_official_cpu_stage_pending", False),
                        ("_gpu_stage_backend", None), ("_gpu_stage_step", None)):
        monkeypatch.setattr(facade, name, value)
    yield
    thread = facade._provision_thread
    if thread is not None:
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize("backend,label", [
    ("cpu", "BEAT · CPU"),
    ("metal", "BEAT · Metal"),
    ("cuda", "BEAT · CUDA"),
    ("rocm", "BEAT · ROCm"),
])
def test_preparation_status_uses_backend_display_label(official, monkeypatch, backend, label):
    monkeypatch.setattr(facade, "_preparation_in_flight", True)
    monkeypatch.setattr(facade, "_requested_backend", backend)
    monkeypatch.setattr(facade, "_provision_step", "precompile")
    monkeypatch.setattr(facade, "_gpu_stage_step", "precompile")
    assert facade.runtime_preparation_status(backend) == (
        "preparing", f"Preparing {label} (step: precompile).",
    )


def test_official_presentation_does_not_load_hbb(official, monkeypatch):
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *args, **kwargs:
                        readiness.BackendReadiness(backend == "cpu", "ready", "compiled proof"))
    assert facade.cpu_runtime_readiness(None).ready
    assert _official_runtime_statuses()["cpu"]["available"]
    assert "server.solver.beat_runtime.cli" in facade.provision_command()


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda", "rocm"])
def test_selection_prepares_only_that_backend(official, monkeypatch, backend):
    begun, finish = threading.Event(), threading.Event()
    calls = []
    monkeypatch.delenv(facade.SKIP_PROVISION_ENV_VAR, raising=False)
    monkeypatch.delenv(facade.SKIP_GPU_PROVISION_ENV_VAR, raising=False)
    monkeypatch.setattr(readiness, "backend_readiness", lambda *a, **kw:
                        readiness.BackendReadiness(False, "unprovisioned", "mock"))
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kw:
                        {backend: {"available": True, "reason": "mock"}})

    def provision(**kwargs):
        calls.append(backend)
        kwargs["step_cb"]("precompile")
        begun.set()
        assert finish.wait(2)
        return {"status": "ready"}

    for name in ("cpu", *hardware.GPU_BACKENDS):
        monkeypatch.setattr(readiness, f"provision_{name}", provision if name == backend
                            else lambda **kw: pytest.fail("unselected backend prepared"))
    try:
        thread = facade.start_selected_provisioning(f"beat-{backend}")
        assert thread is not None and begun.wait(2)
        assert facade.runtime_preparation_status(backend)[0] == "preparing"
        assert all(facade.runtime_preparation_status(name)[0] is None
                   for name in ("cpu", *hardware.GPU_BACKENDS) if name != backend)
        assert facade.start_selected_provisioning(f"beat-{backend}") is thread
    finally:
        finish.set()
    thread.join(2)
    assert calls == [backend]


@pytest.mark.parametrize("engine", [None, "auto", "metal", "bempp"])
def test_non_beat_selection_never_provisions(official, monkeypatch, engine):
    monkeypatch.setattr(readiness, "backend_readiness", lambda *a, **k: pytest.fail("unselected runtime probed"))
    assert facade.start_selected_provisioning(engine) is None


def test_cpu_provisioning_does_not_inventory_or_prepare_gpu(official, monkeypatch):
    calls = []
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kw: pytest.fail("unselected GPU inventoried"))
    monkeypatch.setattr(readiness, "backend_readiness", lambda *a, **kw:
                        readiness.BackendReadiness(False, "unprovisioned", "mock"))
    monkeypatch.setattr(readiness, "provision_cpu", lambda **kw: calls.append("cpu") or {"status": "ready"})
    thread = facade.start_cpu_provisioning(environ={})
    thread.join(2)
    assert calls == ["cpu"]


@pytest.mark.parametrize("state", ["ready", "failed", "package-unusable"])
def test_settled_or_absent_runtime_does_not_retry(official, monkeypatch, state):
    monkeypatch.setattr(readiness, "backend_readiness", lambda *args, **kwargs:
                        readiness.BackendReadiness(state == "ready", state, "recorded"))
    monkeypatch.setattr(readiness, "provision_cpu", lambda **kwargs: pytest.fail("unexpected setup"))
    env = {provider.PROVIDER_ENV: "official", facade.SKIP_GPU_PROVISION_ENV_VAR: "1"}
    assert facade.start_cpu_provisioning(environ=env, system="Linux") is None


def test_skip_cpu_switch_skips_both_stages(official, monkeypatch):
    monkeypatch.setattr(readiness, "backend_readiness", lambda *args, **kwargs: pytest.fail("must skip"))
    assert facade.start_cpu_provisioning(environ={provider.PROVIDER_ENV: "official", facade.SKIP_PROVISION_ENV_VAR: "1"}, system="Windows") is None


def test_invalidation_updates_live_registry_without_hbb(official, monkeypatch):
    usable = set()
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *args, **kwargs:
                        readiness.BackendReadiness(backend in usable, "ready" if backend in usable else "stale", f"{backend} proof"))

    async def scenario():
        registry = EngineRegistry(cpu_refresh=True, detector=lambda: [
            EngineInfo(f"beat-{backend}", False, "initial", None) for backend in readiness.BACKENDS
        ])
        try:
            await registry.capabilities()
            usable.add("cpu")
            readiness.probe_cache_clear()
            await asyncio.wait_for(registry._refresh_cpu_backend(), 2)
            entries = {entry.name: entry for entry in await registry.capabilities()}
            assert entries["beat-cpu"].available and not entries["beat-metal"].available
            assert registry.official_runtime_statuses["cpu"]["available"]
            usable.clear()
            readiness.probe_cache_clear()
            await asyncio.wait_for(registry._refresh_cpu_backend(), 2)
            entries = {entry.name: entry for entry in await registry.capabilities()}
            assert not entries["beat-cpu"].available
        finally:
            await registry.shutdown_prewarm()
        revision = registry._refresh_revision
        readiness.probe_cache_clear()
        assert registry._refresh_revision == revision

    asyncio.run(scenario())


@pytest.mark.parametrize("cpu_pending", [True, False])
def test_macos_platform_reasons_survive_preparation(monkeypatch, tmp_path, cpu_pending):
    from server.solver.beat_runtime import assets

    monkeypatch.setenv(provider.PROVIDER_ENV, "official")
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(facade, "_preparation_in_flight", True)
    monkeypatch.setattr(facade, "_official_cpu_stage_pending", cpu_pending)
    monkeypatch.setattr(facade, "_gpu_stage_backend", None if cpu_pending else "metal")
    monkeypatch.setattr(facade, "_gpu_stage_step", "instantiate")
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(hardware.platform, "mac_ver", lambda: ("14", (), ""))
    def find_only_julia(executable, **kwargs):
        assert executable == "julia", "macOS must not inspect NVIDIA/ROCm"
        return None

    monkeypatch.setattr(hardware.shutil, "which", find_only_julia)

    def absent(*a, **k):
        raise assets.AssetsUnavailable("optional package absent")

    monkeypatch.setattr(readiness, "expected_identity", absent)
    statuses = _official_runtime_statuses()
    for backend, label in (("cuda", "CUDA"), ("rocm", "ROCm")):
        assert statuses[backend]["state"] == "no-device"
        assert statuses[backend]["reason"] == f"{label} requires Linux or Windows"


def test_selected_gpu_receives_only_its_hardware_facts(official, monkeypatch):
    facts = {"cuda": {"available": True, "reason": "mock hardware"}}
    calls = []

    def inventory(**kwargs):
        assert kwargs["only"] == "cuda"
        return facts

    def verdict(backend, **kwargs):
        assert backend == "cuda" and kwargs["hardware_facts"] is facts[backend]
        return readiness.BackendReadiness(False, "unprovisioned", "mock")

    monkeypatch.setattr(hardware, "gpu_hardware", inventory)
    monkeypatch.setattr(readiness, "backend_readiness", verdict)
    monkeypatch.setattr(readiness, "provision_cpu", lambda **kw: pytest.fail("CPU prepared"))
    monkeypatch.setattr(readiness, "provision_cuda", lambda **kw: calls.append(kw["hardware_facts"]) or {"status": "ready"})
    thread = facade._start_official_provisioning({}, backend="cuda")
    thread.join(2)
    assert calls == [facts["cuda"]]


@pytest.mark.parametrize("backend", ["cpu", "metal"])
def test_deselection_terminates_only_owned_preparation_children(official, monkeypatch, backend, tmp_path):
    from types import SimpleNamespace

    monkeypatch.delenv(facade.SKIP_PROVISION_ENV_VAR, raising=False)
    monkeypatch.delenv(facade.SKIP_GPU_PROVISION_ENV_VAR, raising=False)
    monkeypatch.setattr(readiness, "backend_readiness", lambda *a, **kw:
                        readiness.BackendReadiness(False, "unprovisioned", "mock"))
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kw: {backend: {"available": True, "reason": "mock"}})
    entered, stopped = threading.Event(), threading.Event()
    calls = []

    class Output:
        def __iter__(self):
            entered.set()
            assert stopped.wait(2)
            return iter(())
        def close(self):
            pass

    child = SimpleNamespace(stdout=Output(), returncode=None)
    def terminate():
        calls.append("terminate")
        child.returncode = -15
        stopped.set()
    child.terminate = terminate
    child.wait = lambda **kw: child.returncode
    child.poll = lambda: child.returncode
    monkeypatch.setattr(facade.subprocess, "Popen", lambda *a, **kw: child)
    def provision(**kw):
        kw["run_step"]("julia", "nothing", project=tmp_path, environment={}, label="compile", status_cb=lambda _: None)
        pytest.fail("deselected preparation continued")
    monkeypatch.setattr(readiness, f"provision_{backend}", provision)
    thread = facade.start_selected_provisioning(f"beat-{backend}")
    try:
        assert entered.wait(2)
        facade.cancel_unselected_preparation("metal")
    finally:
        stopped.set()
    thread.join(2)
    assert not thread.is_alive()
    assert calls == ["terminate"]
    assert facade.runtime_preparation_status(backend) == (None, None)



def test_deselected_probe_cannot_admit_preparation_after_selection_changes(official, monkeypatch):
    monkeypatch.delenv(facade.SKIP_PROVISION_ENV_VAR, raising=False)
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    def verdict(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return readiness.BackendReadiness(False, "unprovisioned", "mock")
    monkeypatch.setattr(readiness, "backend_readiness", verdict)
    monkeypatch.setattr(readiness, "provision_cpu", lambda **kw: pytest.fail("deselected probe admitted"))
    def check():
        if cancelled.is_set():
            raise ValueError("selection changed")
    errors = []
    def select():
        try:
            facade.start_selected_provisioning("beat-cpu", cancellation_callback=check)
        except ValueError as exc:
            errors.append(str(exc))
    caller = threading.Thread(target=select)
    caller.start()
    try:
        assert entered.wait(2)
        cancelled.set()
        facade.cancel_unselected_preparation("metal")
    finally:
        release.set()
    caller.join(2)
    assert not caller.is_alive() and errors == ["selection changed"]
    assert facade._provision_thread is None
