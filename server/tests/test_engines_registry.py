"""Capability detection and real-dispatch persistence tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from server.engines import registry
from server.engines.dryrun import DryRunEngine
from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime, SymmetryValidationError, resolve_submission
from server.jobs.store import JobStore
from server.solver.base import EngineRunResult
from server.solver.field_traces_store import (
    FieldTraceArtifact,
    FieldTraceChannel,
    METAL_FIELD_TRACE_BACKEND,
)


def test_detection_uses_honest_probe_reasons_and_dryrun_gate(monkeypatch) -> None:
    from server.solver import beat, bempp, circsym, metal

    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "helper loadable", "version": "1"})
    monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": False, "reason": "package absent", "version": None})
    monkeypatch.setattr(beat, "beat_status", lambda: {"available": False, "reason": "no supported GPU", "version": None})
    monkeypatch.setattr(circsym, "circsym_status", lambda: {"available": True, "reason": "meridian ready", "version": "2"})
    detected = registry.detect_engines(environ={"WG2_ENABLE_DRYRUN": "1"})
    assert [(item.name, item.available, item.reason) for item in detected] == [
        ("dryrun", True, "Enabled explicitly by WG2_ENABLE_DRYRUN=1"),
        ("axisym", True, "meridian ready"),
        ("metal", True, "helper loadable"),
        ("bempp", False, "package absent"),
        ("beat", False, "no supported GPU"),
    ]
    assert detected[1].formulations == ("axisymmetric",)
    assert detected[1].mountings == ("free-standing", "infinite-baffle")
    assert detected[1].cancellation_granularity == "intra-frequency"
    assert detected[2].fast_paths == ()
    assert all(item.name != "circsym" for item in detected)


def test_auto_resolution_prefers_metal_then_beat_then_bempp() -> None:
    """AUTO: metal > beat (GPU-only by its own probe) > bempp > dryrun.

    Availability encodes the platform split: Metal is macOS-only and beat
    advertises available only for a functional CUDA/ROCm device, never its
    internal CPU path, so this order cannot route a CPU host onto beat.
    """

    def info(name: str, available: bool) -> registry.EngineInfo:
        return registry.EngineInfo(name, available, "test", "1")

    everything = [info("metal", True), info("beat", True), info("bempp", True)]
    assert registry.resolve_auto_engine(capabilities=everything) == "metal"
    gpu_windows = [info("metal", False), info("beat", True), info("bempp", True)]
    assert registry.resolve_auto_engine(capabilities=gpu_windows) == "beat"
    cpu_windows = [info("metal", False), info("beat", False), info("bempp", True)]
    assert registry.resolve_auto_engine(capabilities=cpu_windows) == "bempp"
    assert registry.get_engine("beat", capabilities=gpu_windows) is not None


def _planner_request(
    *,
    engine: str = "auto",
    solver_mode: str = "auto",
    sim_type: str = "freestanding",
    symmetry: str = "auto",
) -> SolveRequest:
    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "simulation": {
                    "f1": 500,
                    "f2": 8000,
                    "num_frequencies": 3,
                    "sim_type": sim_type,
                },
            },
            "options": {
                "engine": engine,
                "solver_mode": solver_mode,
                "symmetry": symmetry,
            },
        }
    )


def test_formulation_planner_uses_portable_axisym_without_metal(
    monkeypatch,
) -> None:
    from server.solver import circsym

    monkeypatch.setattr(circsym, "axisymmetric_eligibility_reasons", lambda _request: [])
    monkeypatch.setattr(
        circsym,
        "axisymmetric_plan_cost",
        lambda _request, *, full_3d_quadrants: {
            "model": "test",
            "full_3d_quadrants": full_3d_quadrants,
        },
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("axisym", True, "portable CPU", "1"),
            registry.EngineInfo("bempp", True, "Windows CPU fallback", "1"),
        ],
        factory=lambda name: object() if name in {"axisym", "bempp"} else None,
    )

    resolution = asyncio.run(resolve_submission(_planner_request(), engine_registry))

    assert resolution.engine_name == "axisym"
    assert resolution.request.options.engine == "axisym"
    assert resolution.symmetry_metadata["solver_plan"] == {
        "formulation": "axisymmetric",
        "engine": "axisym",
        "reason": "AUTO selected the eligible platform-neutral axisymmetric runner",
        "eligibility_reasons": [],
        "cost_evidence": {"model": "test", "full_3d_quadrants": 1},
    }


def test_axisymmetric_cost_evidence_uses_refined_meridian_and_requested_domain() -> None:
    from server.solver.circsym import axisymmetric_plan_cost

    cost = axisymmetric_plan_cost(_planner_request(), full_3d_quadrants=1)

    assert cost["model"] == "deterministic-reduced-vs-revolved-dense-v1"
    assert cost["frequency_count"] == 3
    assert cost["frequency_max_hz"] == 8000.0
    assert cost["meridian_segments"] > 0
    assert cost["azimuth_quadrature"]["maximum"] >= cost["azimuth_quadrature"][
        "minimum"
    ]
    assert cost["axisymmetric"]["ring_quadrature_terms"] > 0
    assert cost["full_3d_equivalent"]["requested_quadrants"] == 1
    assert cost["full_3d_equivalent"]["domain_fraction"] == 0.25
    assert cost["full_3d_equivalent"]["estimated_triangles"] >= cost[
        "meridian_segments"
    ]
    assert cost["relative_dense_unknowns"] >= 1.0
    assert cost["relative_dense_matrix_memory"] >= 1.0


def test_formulation_planner_falls_back_to_selected_full_3d_backend(
    monkeypatch,
) -> None:
    from server.solver import circsym

    monkeypatch.setattr(
        circsym,
        "axisymmetric_eligibility_reasons",
        lambda _request: ["mouth is not circular"],
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("axisym", True, "portable CPU", "1"),
            registry.EngineInfo("bempp", True, "CPU", "1"),
        ],
        factory=lambda name: object() if name in {"axisym", "bempp"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(_planner_request(engine="bempp"), engine_registry)
    )

    assert resolution.engine_name == "bempp"
    assert resolution.symmetry_metadata["solver_plan"] == {
        "formulation": "full-3d",
        "engine": "bempp",
        "reason": "axisymmetric formulation was not eligible",
        "eligibility_reasons": ["mouth is not circular"],
    }


def test_bempp_coupled_infinite_baffle_planner_requires_full_domain() -> None:
    engine_registry = registry.EngineRegistry(
        detector=lambda: [registry.EngineInfo("bempp", True, "CPU", "1")],
        factory=lambda name: object() if name == "bempp" else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(
                engine="bempp",
                solver_mode="full_3d",
                sim_type="infinite-baffle",
            ),
            engine_registry,
        )
    )
    assert resolution.engine_name == "bempp"
    assert resolution.symmetry_metadata["resolved_quadrants"] == 1234
    assert "validated full-domain" in resolution.symmetry_metadata["solver_plan"][
        "symmetry_reason"
    ]

    with pytest.raises(SymmetryValidationError, match="requires Full symmetry"):
        asyncio.run(
            resolve_submission(
                _planner_request(
                    engine="bempp",
                    solver_mode="full_3d",
                    sim_type="infinite-baffle",
                    symmetry="quarter",
                ),
                engine_registry,
            )
        )


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
            del stage_cb
            cancel_cb()
            if not request.options.polar_config.field_plane:
                return EngineRunResult(
                    results={
                        "frequencies": [500.0],
                        "metadata": {"engine": "fake-metal"},
                    },
                    msh_text=mesh_text,
                    mesh_stats={"vertex_count": 3, "triangle_count": 1},
                    field_trace_unavailable_reason="disabled_by_option",
                )
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

        disabled_request = SolveRequest.model_validate(
            {
                "design": {
                    "formula": "OSSE",
                    "simulation": {"f1": 500, "f2": 501, "num_frequencies": 1},
                },
                "options": {
                    "engine": "metal",
                    "stage_delay_ms": 0,
                    "polar_config": {"field_plane": False},
                },
            }
        )
        disabled_id = await runtime.submit(disabled_request)
        await runtime.wait_idle()
        disabled = await runtime.get_results(disabled_id)
        assert disabled["metadata"]["field_plane_available"] is False
        assert disabled["metadata"]["field_trace_bytes"] is None
        assert disabled["metadata"]["unavailable_reason"] == "disabled_by_option"
        disabled_job = await runtime.get_job(disabled_id)
        assert disabled_job["field_plane_available"] is False
        assert disabled_job["unavailable_reason"] == "disabled_by_option"

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
