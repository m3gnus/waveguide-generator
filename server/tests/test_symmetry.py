from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest

from server.app import create_app
from server.design.schema import DesignConfig
from server.engines.dryrun import DryRunEngine
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime, SymmetryValidationError
from server.jobs.store import JobStore
from server.solver.base import EngineRunResult
from server.solver.metal import MetalEngine
from server.solver.symmetry import resolve_symmetry


def _rosse(**extra) -> DesignConfig:
    return DesignConfig.model_validate(
        {
            "formula": "R-OSSE",
            "R": 150,
            "r0": 12.7,
            "a": 60,
            "a0": 15.5,
            **extra,
        }
    )


def _rotated_guiding_curve() -> DesignConfig:
    return DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 120,
            "r0": 12.7,
            "a": 60,
            "a0": 15.5,
            "guiding_curve": {
                "curve_type": 1,
                "width": 40,
                "aspect_ratio": 1.5,
                "distance": 0.5,
                "rotation": 25,
            },
        }
    )


def test_circular_rosse_resolves_to_quarter() -> None:
    resolution = resolve_symmetry(_rosse())
    assert resolution.quadrants == 1
    assert resolution.xz is True
    assert resolution.yz is True
    assert resolution.reasons == {"xz": [], "yz": []}


def test_design_symmetry_endpoint_returns_the_live_resolution(tmp_path: Path) -> None:
    application = create_app(data_dir=tmp_path)
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/api/design/symmetry"
    )
    response = asyncio.run(endpoint(_rosse()))
    assert response["quadrants"] == 1
    assert response["xz"] is True
    assert response["yz"] is True


def test_rotated_guiding_curve_resolves_full_with_geometric_reasons() -> None:
    resolution = resolve_symmetry(_rotated_guiding_curve())
    assert resolution.quadrants == 1234
    assert resolution.xz is False
    assert resolution.yz is False
    assert "sampled horn surface misses the xz mirror" in resolution.reasons["xz"][0]
    assert "sampled horn surface misses the yz mirror" in resolution.reasons["yz"][0]


def test_vertical_offset_keeps_both_planes() -> None:
    """A rigid +y placement cannot destroy a mirror plane.

    ``_solver_mesher_config`` drops the offset for the y-cut domains, so the
    reduced mesh is still cut on y=0 and reconstructs about y=0.  Vetoing the
    xz plane here used to push every vertically offset design onto a half (or
    full) domain that solves to the same answer at two (or four) times the cost.
    """

    resolution = resolve_symmetry(_rosse(mesh={"vertical_offset": 2}))
    assert resolution.quadrants == 1
    assert resolution.xz is True
    assert resolution.yz is True
    assert resolution.reasons == {"xz": [], "yz": []}


def test_symmetry_contract_documents_vertical_offset_as_rigid_placement() -> None:
    contract = (
        Path(__file__).parents[2] / "docs" / "reference" / "SYMMETRY-CONTRACT.md"
    ).read_text()

    assert "any non-zero or non-scalar value rejects xz" not in contract
    assert "Only a non-finite or non-scalar value rejects xz" in contract
    assert "follows the same finite-scalar rule" in contract


def test_asymmetric_enclosure_spacing_kills_matching_plane() -> None:
    resolution = resolve_symmetry(
        _rosse(
            enclosure={
                "depth": 60,
                "space_l": 20,
                "space_r": 30,
                "space_t": 25,
                "space_b": 25,
            }
        )
    )
    assert resolution.quadrants == 12
    assert resolution.xz is True
    assert resolution.yz is False
    assert "space_l=20" in resolution.reasons["yz"][0]


def test_forced_absent_plane_is_rejected_without_mutating_design(
    tmp_path: Path,
) -> None:
    request = SolveRequest(
        design=_rotated_guiding_curve(),
        options={"engine": "dryrun", "symmetry": "half_xz", "stage_delay_ms": 0},
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("dryrun", True, "test", "builtin")],
                factory=lambda _name: DryRunEngine(),
            ),
        )
        with pytest.raises(SymmetryValidationError, match="xz plane: sampled horn surface"):
            await runtime.submit(request)
        await runtime.shutdown()

    asyncio.run(scenario())
    assert request.design.root.mesh.quadrants is None


def test_auto_override_is_visible_without_rewriting_snapshot(tmp_path: Path) -> None:
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "R-OSSE",
                "R": 150,
                "r0": 12.7,
                "a": 60,
                "a0": 15.5,
                "mesh": {"quadrants": 14},
                "simulation": {"f1": 500, "f2": 1000},
            },
            "options": {"engine": "dryrun", "symmetry": "auto", "stage_delay_ms": 0},
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "record.db"),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("dryrun", True, "test", "builtin")],
                factory=lambda _name: DryRunEngine(),
            ),
        )
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        job = await runtime.get_job(job_id)
        results = await runtime.get_results(job_id)
        assert job["symmetry"]["requested"] == "auto"
        assert job["symmetry"]["resolved_quadrants"] == 1
        assert job["symmetry"]["design_quadrants"] == "14"
        assert job["script_snapshot"]["design"]["mesh"]["quadrants"]["value"] == 14
        assert results["metadata"]["symmetry"]["resolved_quadrants"] == 1
        assert results["metadata"]["solve_path"] == "full-3d"
        assert results["metadata"]["solve_wall_time_seconds"] >= 0
        await runtime.shutdown()

    asyncio.run(scenario())
    assert request.design.root.mesh.quadrants is not None
    assert request.design.root.mesh.quadrants.value == 14


def _metal_request(mode: str = "auto", *, diagonal_angle: float = 45.0) -> SolveRequest:
    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "R-OSSE",
                "R": 150,
                "r0": 12.7,
                "a": 60,
                "a0": 15.5,
                "simulation": {
                    "solver_mode": mode,
                    "f1": 500,
                    "f2": 1000,
                    "num_frequencies": 2,
                },
            },
            "options": {
                "engine": "metal",
                "polar_config": {"inclination": diagonal_angle},
            },
        }
    )


def test_metal_eligible_auto_uses_meridian_path_and_records_it(monkeypatch) -> None:
    from server.solver import circsym

    class FakeCircSym:
        async def run(self, request, **_kwargs):
            return EngineRunResult(results={"metadata": {"solver_backend": "metal"}})

    monkeypatch.setattr(circsym, "circsym_rejection_reasons", lambda _config: [])
    monkeypatch.setattr(
        circsym,
        "circsym_status",
        lambda: {"available": True, "reason": "ready", "version": "1"},
    )
    monkeypatch.setattr(circsym, "CircSymEngine", FakeCircSym)

    async def scenario() -> None:
        outcome = await MetalEngine().run(
            _metal_request(), cancel_cb=lambda: None, stage_cb=lambda *_args: None
        )
        assert outcome.results["metadata"]["solve_path"] == "axisymmetric-meridian"
        assert outcome.results["metadata"]["axisymmetric_eligibility_reasons"] == []

    asyncio.run(scenario())


def test_metal_auto_sends_a_custom_diagonal_to_the_full_3d_path(monkeypatch) -> None:
    from server.solver import circsym, metal

    class ObservationWithoutNativeInclination:
        def __init__(
            self,
            *,
            planes,
            distance_m,
            angle_min_deg,
            angle_max_deg,
            angle_count,
            origin,
            custom_points=None,
        ):
            del (
                planes,
                distance_m,
                angle_min_deg,
                angle_max_deg,
                angle_count,
                origin,
                custom_points,
            )

    class ForbiddenCircSym:
        async def run(self, *_args, **_kwargs):
            pytest.fail("a custom diagonal cannot enter the mesh-free CircSym path")

    async def fake_mesh(*_args, **_kwargs):
        return {
            "msh_text": "mesh",
            "stats": {"triangle_count": 1},
            "metadata": {},
        }

    monkeypatch.setattr(circsym, "circsym_rejection_reasons", lambda _config: [])
    monkeypatch.setattr(
        circsym,
        "circsym_status",
        lambda: {"available": True, "reason": "ready", "version": "1"},
    )
    monkeypatch.setattr(circsym, "ObservationConfig", ObservationWithoutNativeInclination)
    monkeypatch.setattr(circsym, "CircSymEngine", ForbiddenCircSym)
    monkeypatch.setattr(metal, "build_solver_mesh", fake_mesh)
    monkeypatch.setattr(
        metal,
        "solve_metal_from_msh_text",
        lambda *_args, **_kwargs: {"metadata": {"solver_backend": "metal"}},
    )

    async def scenario() -> None:
        outcome = await MetalEngine().run(
            _metal_request(diagonal_angle=35.0),
            cancel_cb=lambda: None,
            stage_cb=lambda *_args: None,
        )
        metadata = outcome.results["metadata"]
        assert metadata["solve_path"] == "full-3d"
        assert metadata["axisymmetric_eligibility_reasons"] == [
            "a non-45-degree diagonal plane requires the full-3D mesh"
        ]

    asyncio.run(scenario())


def test_metal_circsym_eligibility_probe_runs_off_the_event_loop(monkeypatch) -> None:
    from server.solver import circsym

    event_loop_thread = threading.get_ident()
    probe_threads: list[int] = []

    def probe(_config):
        probe_threads.append(threading.get_ident())
        return []

    class FakeCircSym:
        async def run(self, request, **_kwargs):
            return EngineRunResult(results={"metadata": {"solver_backend": "metal"}})

    monkeypatch.setattr(circsym, "circsym_rejection_reasons", probe)
    monkeypatch.setattr(
        circsym,
        "circsym_status",
        lambda: {"available": True, "reason": "ready", "version": "1"},
    )
    monkeypatch.setattr(circsym, "CircSymEngine", FakeCircSym)

    async def scenario() -> None:
        await MetalEngine().run(
            _metal_request(), cancel_cb=lambda: None, stage_cb=lambda *_args: None
        )

    asyncio.run(scenario())
    assert len(probe_threads) == 1
    assert probe_threads[0] != event_loop_thread


def test_metal_ineligible_auto_uses_full_3d_and_records_reasons(monkeypatch) -> None:
    from server.solver import circsym, metal

    async def fake_mesh(*_args, **_kwargs):
        return {
            "msh_text": "mesh",
            "stats": {"triangle_count": 1},
            "metadata": {},
        }

    monkeypatch.setattr(
        circsym,
        "circsym_rejection_reasons",
        lambda _config: ["guiding curve is not circular"],
    )
    monkeypatch.setattr(
        circsym,
        "circsym_status",
        lambda: {"available": True, "reason": "ready", "version": "1"},
    )
    monkeypatch.setattr(metal, "build_solver_mesh", fake_mesh)
    monkeypatch.setattr(
        metal,
        "solve_metal_from_msh_text",
        lambda *_args, **_kwargs: {"metadata": {"solver_backend": "metal"}},
    )

    async def scenario() -> None:
        outcome = await MetalEngine().run(
            _metal_request(), cancel_cb=lambda: None, stage_cb=lambda *_args: None
        )
        metadata = outcome.results["metadata"]
        assert metadata["solve_path"] == "full-3d"
        assert metadata["axisymmetric_eligibility_reasons"] == [
            "guiding curve is not circular"
        ]

    asyncio.run(scenario())
