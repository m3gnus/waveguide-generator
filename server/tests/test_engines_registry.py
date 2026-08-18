"""Capability detection and real-dispatch persistence tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import numpy as np

from server.engines import registry
from server.engines.dryrun import DryRunEngine
from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.solver.base import EngineRunResult
from server.solver.field_traces_store import (
    FieldTraceArtifact,
    FieldTraceChannel,
    METAL_FIELD_TRACE_BACKEND,
)


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
    ]
    assert detected[1].fast_paths == ("axisymmetric-meridian",)
    assert all(item.name != "circsym" for item in detected)


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


def test_runtime_exposes_fresh_field_traces_and_marks_legacy_results(
    tmp_path: Path,
) -> None:
    mesh_text = """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
3
1 0 0 0
2 1 0 0
3 0 1 0
$EndNodes
$Elements
1
1 2 2 1 1 1 2 3
$EndElements
"""

    class FakeMetal:
        name = "metal"

        async def run(self, request, *, cancel_cb, stage_cb):
            del request, stage_cb
            cancel_cb()
            return EngineRunResult(
                results={"frequencies": [500.0], "metadata": {"engine": "fake-metal"}},
                msh_text=mesh_text,
                mesh_stats={"vertex_count": 3, "triangle_count": 1},
                field_traces=FieldTraceArtifact(
                    mesh_text=mesh_text,
                    frequencies_hz=np.asarray([500.0]),
                    k_real=np.asarray([9.159]),
                    k_imag=np.asarray([0.045795]),
                    symmetry_plane=None,
                    solve_path="full-3d",
                    channels=(
                        FieldTraceChannel(
                            "default",
                            np.ones((1, 3), dtype=np.complex128),
                            np.ones((1, 1), dtype=np.complex128),
                        ),
                    ),
                    backend=METAL_FIELD_TRACE_BACKEND,
                ),
            )

    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "simulation": {"f1": 500, "f2": 501, "num_frequencies": 1},
            },
            "options": {"engine": "metal", "stage_delay_ms": 0},
        }
    )

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        runtime = JobRuntime(
            store,
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("metal", True, "test", "1")],
                factory=lambda _name: FakeMetal(),
            ),
        )
        fresh_id = await runtime.submit(request)
        await runtime.wait_idle()

        fresh = await runtime.get_results(fresh_id)
        assert fresh["metadata"]["field_plane_available"] is True
        assert fresh["metadata"]["field_trace_bytes"] == 32
        assert fresh["metadata"]["unavailable_reason"] is None
        fresh_job = await runtime.get_job(fresh_id)
        assert fresh_job["field_plane_available"] is True
        assert fresh_job["field_trace_bytes"] == 32

        now = datetime.now().isoformat()
        store.create_job(
            {
                "id": "legacy",
                "status": "complete",
                "created_at": now,
                "updated_at": now,
                "queued_at": now,
                "completed_at": now,
                "progress": 1.0,
                "stage": "complete",
                "stage_message": "complete",
                "config_json": request.model_dump(mode="json"),
                "config_summary_json": {"formula_type": "OSSE"},
                "task_metadata": {},
            }
        )
        store.store_results("legacy", {"frequencies": [500.0], "metadata": {}})
        legacy = await runtime.get_results("legacy")
        assert legacy["metadata"] == {
            "field_plane_available": False,
            "field_trace_bytes": None,
            "unavailable_reason": "solve_predates_traces",
        }
        legacy_job = await runtime.get_job("legacy")
        assert legacy_job["field_plane_available"] is False
        assert legacy_job["unavailable_reason"] == "solve_predates_traces"
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


def test_real_runtime_persists_advisory_mesh_warning_in_job_log(
    tmp_path: Path, monkeypatch
) -> None:
    warning = (
        "Large solve mesh: 7,173 triangles against a warning threshold of "
        "4,500. The solve will continue."
    )

    class WarningMetal:
        name = "metal"

        async def run(self, request, *, cancel_cb, stage_cb, artifact_cb):
            cancel_cb()
            await artifact_cb(
                "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                {"triangle_count": 7_173, "warnings": [warning]},
            )
            return EngineRunResult(
                results={
                    "frequencies": [500.0],
                    "directivity": {},
                    "spl_on_axis": {"frequencies": [500.0], "spl": [90.0]},
                    "metadata": {},
                },
            )

    request = SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "simulation": {"f1": 500, "f2": 501}},
            "options": {"engine": "metal"},
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "warning.db"),
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("metal", True, "test", "1")],
                factory=lambda _name: WarningMetal(),
            ),
        )
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        job = await runtime.get_job(job_id)
        assert job["status"] == "complete"
        assert job["mesh_stats"]["warnings"] == [warning]
        assert warning in job["log_tail"]
        await runtime.shutdown()

    asyncio.run(scenario())
