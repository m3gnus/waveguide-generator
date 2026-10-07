from __future__ import annotations

import asyncio
import threading

import pytest

from server.engines.registry import EngineInfo, EngineRegistry, _official_runtime_statuses
from server.solver import beat, beat_cpu_runtime as facade
from server.solver.beat_runtime import hardware, provider, readiness


@pytest.fixture
def official(monkeypatch, tmp_path):
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs: {name: {"available": name == "metal", "reason": "mock hardware"} for name in hardware.GPU_BACKENDS})
    monkeypatch.setenv(provider.PROVIDER_ENV, "official")
    for name, value in (("_provision_thread", None), ("_provision_step", None),
                        ("_preparation_in_flight", False), ("_official_cpu_stage_pending", False), ("_runtimes_prepared", False),
                        ("_gpu_stage_backend", None), ("_gpu_stage_step", None)):
        monkeypatch.setattr(facade, name, value)
    yield
    thread = facade._provision_thread
    if thread is not None:
        thread.join(2)
        assert not thread.is_alive()


def test_provider_is_explicit_and_default_off():
    assert not provider.official_selected({})
    assert not provider.official_selected({provider.PROVIDER_ENV: "hbb"})
    assert provider.official_selected({provider.PROVIDER_ENV: "official"})


def test_official_presentation_does_not_load_hbb(official, monkeypatch):
    monkeypatch.setattr(beat, "_load_api", lambda: pytest.fail("HBB readiness import"))
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *args, **kwargs:
                        readiness.BackendReadiness(backend == "cpu", "ready", "compiled proof"))
    assert facade.cpu_runtime_readiness(None).ready
    assert _official_runtime_statuses()["cpu"]["available"]
    assert "server.solver.beat_runtime.cli" in facade.provision_command()


def test_alternative_does_not_change_solve_api(official, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(beat, "_beat", sentinel)
    assert beat._load_api() is sentinel
    assert beat._load_readiness_api() is sentinel


@pytest.mark.parametrize("backend,system", [("metal", "Darwin"), ("cuda", "Windows"), ("rocm", "Linux")])
def test_background_stages_publish_progress_and_preserve_cpu(official, monkeypatch, tmp_path, backend, system):
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs:
                        {name: {"available": name == backend, "reason": "mock hardware"} for name in hardware.GPU_BACKENDS})
    begun, finish = threading.Event(), threading.Event()
    ready = set()
    observed = []
    calls = []

    def verdict(backend, *args, **kwargs):
        return readiness.BackendReadiness(backend in ready, "ready" if backend in ready else "unprovisioned", backend)

    def cpu(**kwargs):
        calls.append(("cpu", kwargs["environ"], threading.current_thread().name))
        kwargs["step_cb"]("probe")
        kwargs["status_cb"]("probe")
        begun.set()
        assert finish.wait(2)
        ready.add("cpu")
        return {"status": "ready"}

    def gpu(**kwargs):
        calls.append((backend, kwargs["environ"], threading.current_thread().name))
        kwargs["step_cb"]("offline")
        kwargs["status_cb"]("offline")
        statuses = _official_runtime_statuses()
        assert statuses["cpu"]["available"] and statuses["cpu"]["state"] == "ready"
        assert facade.cpu_runtime_readiness(None).ready
        assert all(statuses[name]["state"] != "provisioning" for name in hardware.GPU_BACKENDS if name != backend)
        status = statuses[backend]
        assert not status["available"] and status["state"] == "provisioning"
        assert "offline" in status["reason"]
        return {"status": "failed"}

    monkeypatch.setattr(readiness, "backend_readiness", verdict)
    monkeypatch.setattr(readiness, "provision_cpu", cpu)
    monkeypatch.setattr(readiness, f"provision_{backend}", gpu)
    monkeypatch.setattr(facade, "_import", lambda name: pytest.fail("HBB import"))
    listener = lambda: observed.append((facade.cpu_preparation_in_flight(), facade.cpu_provisioning_step(), facade.gpu_preparation_reason(backend)))
    facade.add_readiness_listener(listener)
    env = {provider.PROVIDER_ENV: "official", "WG2_BEAT_RUNTIME_DIR": str(tmp_path / "runtime")}
    try:
        thread = facade.start_cpu_provisioning(environ=env, system=system)
        assert thread is not None and begun.wait(2)
        assert facade.cpu_runtime_readiness(None).state == "provisioning"
        assert _official_runtime_statuses()["cpu"]["state"] == "provisioning"
        assert facade.start_cpu_provisioning(environ=env, system=system) is thread
        finish.set()
        thread.join(2)
        assert not thread.is_alive() and not facade.cpu_preparation_in_flight()
        assert [row[0] for row in calls] == ["cpu", backend]
        assert all(row[1] == env and row[2] == facade.PROVISION_THREAD_NAME for row in calls)
        assert facade.cpu_runtime_readiness(None).ready
        assert any(row[1] == "probe" for row in observed)
        assert any(row[2] and "offline" in row[2] for row in observed)
        assert observed[-1] == (False, None, None)
    finally:
        finish.set()
        facade.remove_readiness_listener(listener)


@pytest.mark.parametrize("failure", ["detection", "cpu"])
def test_background_failure_does_not_hide_other_stage(official, monkeypatch, failure):
    calls = []
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs:
                        {name: {"available": name == "cuda", "reason": "mock hardware"} for name in hardware.GPU_BACKENDS})
    monkeypatch.setattr(readiness, "backend_readiness", lambda *a, **k:
                        readiness.BackendReadiness(False, "unprovisioned", "mock"))

    def detect(**kwargs):
        assert calls == ["cpu"]
        assert threading.current_thread().name == facade.PROVISION_THREAD_NAME
        if failure == "detection":
            raise OSError("inventory failed")
        return "cuda"

    def cpu(**kwargs):
        calls.append("cpu")
        if failure == "cpu":
            raise OSError("CPU offline")
        return {"status": "ready"}

    monkeypatch.setattr(hardware, "detect_gpu_backend", detect)
    monkeypatch.setattr(readiness, "provision_cpu", cpu)
    monkeypatch.setattr(readiness, "provision_cuda", lambda **kwargs:
                        calls.append("cuda") or {"status": "ready"})
    thread = facade.start_cpu_provisioning(environ={provider.PROVIDER_ENV: "official"}, system="Windows")
    assert thread is not None
    thread.join(2)
    assert not thread.is_alive() and not facade.cpu_preparation_in_flight()
    assert calls == (["cpu"] if failure == "detection" else ["cpu", "cuda"])


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
    monkeypatch.setattr(beat, "_load_api", lambda: None)
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


def test_launcher_reuses_verified_inventory_after_cpu_finishes(official, monkeypatch):
    calls = []
    facts = {name: {"available": name == "cuda", "reason": "mock hardware"} for name in hardware.GPU_BACKENDS}
    ready = set()

    def inventory(**kwargs):
        if not kwargs.get("probe_nvidia", True):
            return facts
        assert "cpu" in ready
        assert facade.cpu_runtime_readiness(None).ready
        calls.append("detect")
        return facts

    def verdict(backend, **kwargs):
        if backend == "cuda":
            assert kwargs["hardware_facts"] is facts[backend]
        return readiness.BackendReadiness(backend in ready, "ready" if backend in ready else "unprovisioned", "mock")

    def cpu(**kwargs):
        ready.add("cpu")
        calls.append("cpu")
        return {"status": "ready"}

    def cuda(**kwargs):
        assert kwargs["hardware_facts"] is facts["cuda"]
        calls.append("cuda")
        return {"status": "ready"}

    monkeypatch.setattr(hardware, "gpu_hardware", inventory)
    monkeypatch.setattr(readiness, "backend_readiness", verdict)
    monkeypatch.setattr(readiness, "provision_cpu", cpu)
    monkeypatch.setattr(readiness, "provision_cuda", cuda)
    thread = facade.start_cpu_provisioning(environ={provider.PROVIDER_ENV: "official"})
    assert thread is not None
    thread.join(2)
    assert not thread.is_alive()
    assert calls == ["cpu", "detect", "cuda"]
