from __future__ import annotations

import asyncio
import threading

import pytest

from server.engines.registry import EngineInfo, EngineRegistry
from server.solver import beat, beat_cpu_runtime as facade
from server.solver.beat_runtime import provider, readiness


@pytest.fixture
def official(monkeypatch):
    monkeypatch.setenv(provider.PROVIDER_ENV, "official")
    for name, value in (("_provision_thread", None), ("_provision_step", None),
                        ("_preparation_in_flight", False), ("_runtimes_prepared", False),
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
    assert beat.beat_status()["backend"] == "cpu"
    assert beat.beat_backend_statuses()["cpu"]["available"]
    assert beat.reprobe_package_backend_statuses()["cpu"]["available"]
    assert "server.solver.beat_runtime.cli --backend cpu" in facade.provision_command()


def test_alternative_does_not_change_solve_api(official, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(beat, "_beat", sentinel)
    assert beat._load_api() is sentinel
    assert beat._load_readiness_api() is readiness


def test_background_stages_publish_progress_and_preserve_cpu(official, monkeypatch, tmp_path):
    begun, finish = threading.Event(), threading.Event()
    ready = set()
    observed = []
    calls = []

    def verdict(backend, *args, **kwargs):
        return readiness.BackendReadiness(backend in ready, "ready" if backend in ready else "unprovisioned", backend)

    def cpu(**kwargs):
        calls.append(("cpu", kwargs["environ"], threading.current_thread().name))
        kwargs["status_cb"]("probe")
        begun.set()
        assert finish.wait(2)
        ready.add("cpu")
        return {"status": "ready"}

    def metal(**kwargs):
        calls.append(("metal", kwargs["environ"], threading.current_thread().name))
        kwargs["status_cb"]("offline")
        status = beat.beat_backend_statuses()["metal"]
        assert not status["available"] and status["state"] == "provisioning"
        assert "offline" in status["reason"]
        return {"status": "failed"}

    monkeypatch.setattr(readiness, "backend_readiness", verdict)
    monkeypatch.setattr(readiness, "provision_cpu", cpu)
    monkeypatch.setattr(readiness, "provision_metal", metal)
    monkeypatch.setattr(facade, "_import", lambda name: pytest.fail("HBB import"))
    listener = lambda: observed.append((facade.cpu_preparation_in_flight(), facade.cpu_provisioning_step(), facade.gpu_preparation_reason("metal")))
    facade.add_readiness_listener(listener)
    env = {provider.PROVIDER_ENV: "official", "WG2_BEAT_RUNTIME_DIR": str(tmp_path / "runtime")}
    try:
        thread = facade.start_cpu_provisioning(environ=env, system="Darwin")
        assert thread is not None and begun.wait(2)
        assert facade.cpu_runtime_readiness(None).state == "provisioning"
        assert beat.beat_backend_statuses()["cpu"]["state"] == "provisioning"
        assert facade.start_cpu_provisioning(environ=env, system="Darwin") is thread
        finish.set()
        thread.join(2)
        assert not thread.is_alive() and not facade.cpu_preparation_in_flight()
        assert [row[0] for row in calls] == ["cpu", "metal"]
        assert all(row[1] == env and row[2] == facade.PROVISION_THREAD_NAME for row in calls)
        assert facade.cpu_runtime_readiness(None).ready
        assert any(row[1] == "probe" for row in observed)
        assert any(row[2] and "offline" in row[2] for row in observed)
        assert observed[-1] == (False, None, None)
    finally:
        finish.set()
        facade.remove_readiness_listener(listener)


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
    monkeypatch.setattr(beat, "_load_api", lambda: pytest.fail("HBB optional package"))
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
