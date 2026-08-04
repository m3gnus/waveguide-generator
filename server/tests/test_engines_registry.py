"""Capability detection and real-dispatch persistence tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from server.engines import registry
from server.engines.dryrun import DryRunEngine
from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.solver.base import EngineRunResult


def test_detection_uses_honest_probe_reasons_and_dryrun_gate(monkeypatch) -> None:
    from server.solver import bempp, circsym, metal

    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "helper loadable", "version": "1"})
    monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": False, "reason": "package absent", "version": None})
    monkeypatch.setattr(circsym, "circsym_status", lambda: {"available": True, "reason": "meridian ready", "version": "2"})
    detected = registry.detect_engines(environ={"WG2_ENABLE_DRYRUN": "1"})
    assert [(item.name, item.available, item.reason) for item in detected] == [
        ("dryrun", True, "Enabled explicitly by WG2_ENABLE_DRYRUN=1"),
        ("metal", True, "helper loadable"),
        ("bempp", False, "package absent"),
        ("circsym", True, "meridian ready"),
    ]


def test_capability_snapshot_is_reused_by_solve_submission(tmp_path: Path) -> None:
    calls = 0

    def detect_once() -> list[registry.EngineInfo]:
        nonlocal calls
        calls += 1
        return [registry.EngineInfo("dryrun", True, "test", "builtin")]

    shared_registry = registry.EngineRegistry(
        detector=detect_once, factory=lambda _name: DryRunEngine()
    )
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "simulation": {"f1": 500, "f2": 501},
            },
            "options": {"engine": "dryrun", "stage_delay_ms": 0},
        }
    )

    async def scenario() -> None:
        assert (await shared_registry.capabilities())[0].available is True
        runtime = JobRuntime(
            JobStore(tmp_path / "cached.db"), engine_registry=shared_registry
        )
        await runtime.submit(request)
        await runtime.wait_idle()
        await runtime.shutdown()

    asyncio.run(scenario())
    assert calls == 1


def test_real_runtime_seam_persists_artifact_stats_results_and_stages(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeMetal:
        name = "metal"

        async def run(self, request, *, cancel_cb, stage_cb):
            cancel_cb()
            stage_cb("mesh_prepare", 0.5, "Building real mesh")
            stage_cb("frequency_solve", 0.5, "Solving native frequency")
            return EngineRunResult(
                results={
                    "frequencies": [500.0],
                    "directivity": {},
                    "spl_on_axis": {"frequencies": [500.0], "spl": [90.0], "phase_degrees": [0.0]},
                    "impedance": {"frequencies": [500.0], "real": [1.0], "imaginary": [0.0]},
                    "di": {"frequencies": [500.0], "di": {}},
                    "metadata": {"engine": "fake-metal"},
                },
                msh_text="$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                mesh_stats={"vertex_count": 3, "triangle_count": 1},
            )

    monkeypatch.setattr("server.jobs.runtime.get_engine", lambda name: FakeMetal())
    request = SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "simulation": {"f1": 500, "f2": 501, "num_frequencies": 1}},
            "options": {"engine": "metal", "stage_delay_ms": 0},
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("metal", True, "test", "1")],
                factory=lambda _name: FakeMetal(),
            ),
        )
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        job = await runtime.get_job(job_id)
        assert job["status"] == "complete"
        assert job["has_mesh_artifact"] is True
        assert job["mesh_stats"]["triangle_count"] == 1
        assert (await runtime.get_results(job_id))["metadata"]["engine"] == "fake-metal"
        assert "$MeshFormat" in await runtime.get_mesh_artifact(job_id)
        event_types = [event["type"] for event in runtime.store.replay_events(0)]
        assert "stage" in event_types
        assert "log" in event_types
        assert event_types[-1] == "completed"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_real_runtime_persists_mesh_before_a_native_solve_failure(
    tmp_path: Path, monkeypatch
) -> None:
    class FailingMetal:
        name = "metal"

        async def run(self, request, *, cancel_cb, stage_cb, artifact_cb):
            cancel_cb()
            await artifact_cb("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n", {"triangle_count": 7})
            raise RuntimeError("native solve failed")

    monkeypatch.setattr("server.jobs.runtime.get_engine", lambda name: FailingMetal())
    request = SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "simulation": {"f1": 500, "f2": 501}},
            "options": {"engine": "metal"},
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "failed.db"),
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("metal", True, "test", "1")],
                factory=lambda _name: FailingMetal(),
            ),
        )
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        job = await runtime.get_job(job_id)
        assert job["status"] == "error"
        assert job["has_mesh_artifact"] is True
        assert job["mesh_stats"]["triangle_count"] == 7
        assert "$MeshFormat" in await runtime.get_mesh_artifact(job_id)
        await runtime.shutdown()

    asyncio.run(scenario())
