"""Mock every provisioner: these tests never start Julia or download packages."""

import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server.diagnostics.capabilities import capabilities_payload
from server.engines.registry import _beat_engine_info
from server.engines.runtime_api import mount_runtime_preparation
from server.solver import beat_cpu_runtime as runtime
from server.solver.beat_runtime import hardware, readiness


@pytest.fixture
def preparation(monkeypatch):
    monkeypatch.delenv(runtime.SKIP_PROVISION_ENV_VAR, raising=False)
    monkeypatch.delenv(runtime.SKIP_GPU_PROVISION_ENV_VAR, raising=False)
    monkeypatch.setattr(runtime.platform, "system", lambda: "Linux")
    for name, value in (("_provision_thread", None), ("_provision_step", None),
                        ("_requested_backend", None), ("_preparation_errors", {}),
                        ("_runtimes_prepared", False),
                        ("_preparation_in_flight", False), ("_official_cpu_stage_pending", False),
                        ("_gpu_stage_backend", None), ("_gpu_stage_step", None)):
        monkeypatch.setattr(runtime, name, value)
    release, entered = threading.Event(), threading.Event()
    records = {backend: {"available": False, "state": "unprovisioned", "reason": "Runtime needs preparation."}
               for backend in readiness.BACKENDS}
    outcome, calls = {"status": "ready"}, []
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **options:
                        {backend: {"available": True, "reason": "Supported device"} for backend in hardware.GPU_BACKENDS})
    monkeypatch.setattr(hardware, "detect_gpu_backend", lambda **options: "metal")
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, **options:
                        readiness.BackendReadiness(records[backend]["available"], records[backend]["state"], records[backend]["reason"]))

    def provision(backend, **options):
        calls.append((backend, options))
        options["step_cb"]("downloading")
        entered.set()
        assert release.wait(5), "Test did not release its preparation worker"
        result = dict(outcome)
        records[backend].update(available=result["status"] == "ready", state=result["status"],
                                reason=result.get("error", "Runtime ready."))
        return result

    for backend in readiness.BACKENDS:
        monkeypatch.setattr(readiness, f"provision_{backend}",
                            lambda backend=backend, **options: provision(backend, **options))

    class Registry:
        async def capabilities(self):
            return tuple(_beat_engine_info(backend, record) for backend, record in records.items())

        def cpu_preparation_in_flight(self):
            return runtime.cpu_preparation_in_flight()

    registry, app = Registry(), FastAPI()
    mount_runtime_preparation(app)

    @app.get("/api/capabilities")
    async def capabilities():
        return await capabilities_payload(registry)

    try:
        with TestClient(app) as client:
            yield client, release, entered, records, outcome, calls
    finally:
        release.set()
        if runtime._provision_thread is not None:
            runtime._provision_thread.join(5)
            assert not runtime._provision_thread.is_alive()


def rows(client):
    return {row["name"]: row for row in client.get("/api/capabilities").json()["engines"]}


@pytest.mark.parametrize("backend", readiness.BACKENDS)
def test_start_progress_conflict_and_ready(preparation, backend):
    client, release, entered, records, outcome, calls = preparation
    assert rows(client)[f"beat-{backend}"]["runtime_state"] == "unprepared"
    assert calls == []
    response = client.post(f"/api/runtimes/beat/{backend}/prepare")
    assert response.status_code == 202
    assert response.json() == {"backend": backend, "state": "preparing"}
    assert entered.wait(2)
    row = rows(client)[f"beat-{backend}"]
    assert row["runtime_state"] == "preparing"
    assert "downloading" in row["reason"]
    assert not row["available"]
    assert client.get("/api/capabilities").json()["cpuPreparationInFlight"]
    refused = client.post("/api/runtimes/beat/cpu/prepare")
    assert refused.status_code == 409
    assert "already running" in refused.json()["detail"]
    release.set()
    runtime._provision_thread.join(2)
    assert rows(client)[f"beat-{backend}"]["runtime_state"] == "ready"
    assert rows(client)[f"beat-{backend}"]["available"]
    assert not client.get("/api/capabilities").json()["cpuPreparationInFlight"]
    assert len(calls) == 1 and calls[0][0] == backend
    assert calls[0][1]["retry"] is True


@pytest.mark.parametrize("backend", readiness.BACKENDS)
def test_failure_surfaces_and_can_retry(preparation, backend):
    client, release, entered, records, outcome, calls = preparation
    outcome.update(status="failed", error="Network unavailable")
    release.set()
    assert client.post(f"/api/runtimes/beat/{backend}/prepare").status_code == 202
    runtime._provision_thread.join(2)
    row = rows(client)[f"beat-{backend}"]
    assert row["runtime_state"] == "failed" and row["runtime_preparable"]
    assert "Network unavailable" in row["reason"] and "Run:" not in row["reason"]
    outcome.update(status="ready")
    assert client.post(f"/api/runtimes/beat/{backend}/prepare").status_code == 202
    runtime._provision_thread.join(2)
    assert rows(client)[f"beat-{backend}"]["available"]
    assert len(calls) == 2


def test_unexpected_failure_surfaces(preparation, monkeypatch):
    client, release, entered, records, outcome, calls = preparation

    def broken(**options):
        raise RuntimeError("Cannot write runtime directory")

    monkeypatch.setattr(readiness, "provision_cpu", broken)
    assert client.post("/api/runtimes/beat/cpu/prepare").status_code == 202
    runtime._provision_thread.join(2)
    row = rows(client)["beat-cpu"]
    assert row["runtime_state"] == "failed" and row["runtime_preparable"]
    assert "Cannot write runtime directory" in row["reason"]


@pytest.mark.parametrize("setting,backend", [(runtime.SKIP_PROVISION_ENV_VAR, "cpu"),
                                             (runtime.SKIP_PROVISION_ENV_VAR, "metal"),
                                             (runtime.SKIP_GPU_PROVISION_ENV_VAR, "cuda")])
def test_opt_out_refused(preparation, monkeypatch, setting, backend):
    client, release, entered, records, outcome, calls = preparation
    monkeypatch.setenv(setting, "1")
    assert client.post(f"/api/runtimes/beat/{backend}/prepare").status_code == 400
    assert not rows(client)[f"beat-{backend}"]["runtime_preparable"]
    assert "Preparation is disabled" in rows(client)[f"beat-{backend}"]["reason"]
    assert calls == []


@pytest.mark.parametrize("state", ["no-device", "package-unusable", "unsupported", "identity-unavailable", "ready"])
def test_unavailable_or_ready_runtime_refused(preparation, state):
    client, release, entered, records, outcome, calls = preparation
    records["cuda"].update(state=state, available=state == "ready", reason="Cannot prepare this runtime")
    assert client.post("/api/runtimes/beat/cuda/prepare").status_code == 400
    assert calls == []


def test_external_preparation_and_unknown_backend_refused(preparation):
    client, release, entered, records, outcome, calls = preparation
    records["cpu"].update(state="provisioning", reason="Another process is preparing CPU")
    response = client.post("/api/runtimes/beat/cpu/prepare")
    assert response.status_code == 409 and "Another process" in response.json()["detail"]
    assert client.post("/api/runtimes/beat/unknown/prepare").status_code == 422
    assert calls == []


def test_launch_worker_and_explicit_preparation_share_exclusion(preparation):
    client, release, entered, records, outcome, calls = preparation
    worker = runtime.start_cpu_provisioning(environ={})
    assert worker is not None and entered.wait(2)
    assert client.post("/api/runtimes/beat/cpu/prepare").status_code == 409
    release.set()
    worker.join(2)


def test_preparation_in_another_process_refuses_all_backends(preparation, monkeypatch):
    from server.solver.beat_runtime import locks

    client, release, entered, records, outcome, calls = preparation
    monkeypatch.setattr(locks, "provisioning_active", lambda: True)
    response = client.post("/api/runtimes/beat/cpu/prepare")
    assert response.status_code == 409
    assert "Another process" in response.json()["detail"]
    assert calls == []


def test_manual_recovery_supersedes_unexpected_failure(preparation):
    client, release, entered, records, outcome, calls = preparation
    runtime._preparation_errors["cpu"] = "Previous local error"
    records["cpu"].update(available=True, state="ready")
    row = rows(client)["beat-cpu"]
    assert row["available"] and row["runtime_state"] == "ready"


@pytest.mark.parametrize("state", ["stale", "no-julia", "interrupted"])
def test_stale_missing_or_interrupted_runtime_can_prepare(preparation, state):
    client, release, entered, records, outcome, calls = preparation
    records["cpu"].update(state=state)
    assert rows(client)["beat-cpu"]["runtime_state"] == "unprepared"
    assert rows(client)["beat-cpu"]["runtime_preparable"]
    release.set()
    assert client.post("/api/runtimes/beat/cpu/prepare").status_code == 202
    runtime._provision_thread.join(2)
    assert rows(client)["beat-cpu"]["available"]
