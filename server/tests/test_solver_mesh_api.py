"""POST /api/solver-mesh: the viewport's window onto the real solver artifact."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.design.schema import DesignConfig
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.mesh_preview import mount_solver_mesh, solver_mesh_response
from server.jobs.models import SolveOptions, SolveRequest
from server.jobs.runtime.core import (
    EngineUnavailableError,
    _execution_request,
    resolve_submission,
)
from server.mesh import builder as mesh_builder
from server.mesh.builder import clear_solver_mesh_cache


async def _preview_response(*args, **kwargs):
    return await solver_mesh_response(
        *args, engine_registry=EngineRegistry(detector=lambda: []), **kwargs
    )


def _rosse(**extra: Any) -> DesignConfig:
    """Circular R-OSSE: resolves to a quarter domain (see test_symmetry.py)."""

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


STUB_MSH = "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"


def _stub_worker(calls: list[int]):
    async def worker(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        return {
            "msh_text": STUB_MSH,
            "stats": {
                "triangle_count": 4,
                "vertex_count": 4,
                "warnings": [],
            },
            "integrity": {"valid": True},
            "metadata": {},
        }

    return worker


def test_response_shape_cut_planes_and_cache_key_stability(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(mesh_builder, "run_mesh_build", _stub_worker(calls))
    clear_solver_mesh_cache()

    async def scenario() -> None:
        first = await _preview_response(_rosse(), "auto")
        assert first["msh_text"] == STUB_MSH
        assert first["stats"]["triangle_count"] == 4
        assert first["stats"]["vertex_count"] == 4
        assert first["stats"]["warnings"] == []
        assert first["stats"]["mesh_cache_hit"] is False
        assert isinstance(first["stats"]["mesh_cache_key"], str)
        assert len(first["stats"]["mesh_cache_key"]) == 64
        # Circular R-OSSE resolves to the quarter domain: both origin cuts.
        assert first["quadrants"] == 1
        assert first["cut_planes"] == ["x0", "y0"]

        second = await _preview_response(_rosse(), "auto")
        assert second["stats"]["mesh_cache_key"] == first["stats"]["mesh_cache_key"]
        assert second["stats"]["mesh_cache_hit"] is True

        # Frequency edits act after mesh generation and must be cache hits.
        frequency_edit = await _preview_response(
            _rosse(simulation={"f1": 500, "f2": 5_000, "num_frequencies": 20}),
            "auto",
        )
        assert (
            frequency_edit["stats"]["mesh_cache_key"]
            == first["stats"]["mesh_cache_key"]
        )
        assert frequency_edit["stats"]["mesh_cache_hit"] is True

        # A geometry edit is a different artifact.
        geometry_edit = await _preview_response(_rosse(R=170), "auto")
        assert (
            geometry_edit["stats"]["mesh_cache_key"]
            != first["stats"]["mesh_cache_key"]
        )

    try:
        asyncio.run(scenario())
        assert len(calls) == 2
    finally:
        clear_solver_mesh_cache()


def test_requested_domain_follows_the_solve_symmetry_mode(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(mesh_builder, "run_mesh_build", _stub_worker(calls))
    clear_solver_mesh_cache()

    async def scenario() -> None:
        full = await _preview_response(_rosse(), "full")
        assert full["quadrants"] == 1234
        assert full["cut_planes"] == []

        half = await _preview_response(_rosse(), "half_xz")
        assert half["quadrants"] == 12
        assert half["cut_planes"] == ["y0"]

        # Requesting a mirror plane the geometry lacks is a 422, not a build.
        with pytest.raises(HTTPException) as refusal:
            await _preview_response(
                _rosse(
                    enclosure={
                        "depth": 60,
                        "space_l": 20,
                        "space_r": 30,
                        "space_t": 25,
                        "space_b": 25,
                    }
                ),
                "quarter",
            )
        assert refusal.value.status_code == 422

    try:
        asyncio.run(scenario())
        # full and half_xz stamp different quadrants: two distinct artifacts.
        assert len(calls) == 2
    finally:
        clear_solver_mesh_cache()


def test_disconnect_flag_abandons_the_build_as_499(monkeypatch) -> None:
    async def worker(fn, design_dump, *args: Any, cancel_cb, **kwargs: Any):
        # Yield so the disconnect watcher task gets its first poll in, the way
        # a real build yields by running on the worker thread.
        await asyncio.sleep(0.01)
        cancel_cb()
        raise AssertionError("cancel checkpoint should have raised")

    monkeypatch.setattr(mesh_builder, "run_mesh_build", worker)
    clear_solver_mesh_cache()

    async def scenario() -> None:
        async def already_gone() -> bool:
            return True

        with pytest.raises(HTTPException) as refusal:
            await _preview_response(_rosse(), "auto", already_gone)
        assert refusal.value.status_code == 499

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()


def test_real_build_returns_a_parseable_reduced_artifact() -> None:
    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    clear_solver_mesh_cache()

    async def scenario() -> None:
        response = await _preview_response(_rosse(), "auto")
        assert response["msh_text"].lstrip().startswith("$MeshFormat")
        assert response["stats"]["triangle_count"] > 0
        assert response["cut_planes"] == ["x0", "y0"]
        # The artifact entered the shared cache under the solve's own key.
        again = await _preview_response(_rosse(), "auto")
        assert again["stats"]["mesh_cache_hit"] is True
        assert (
            again["stats"]["mesh_cache_key"] == response["stats"]["mesh_cache_key"]
        )

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()


@pytest.mark.parametrize(
    ("design_extra", "options", "quadrants", "wall"),
    [
        ({}, {"ground_plane": {"enabled": True, "axis": "y", "height_m": 1.0}}, 14, 5),
        ({"simulation": {"sim_type": "infinite-baffle"}}, {"engine": "bempp"}, 1234, None),
        ({"mesh": {"wall_thickness": 0}}, {"engine": "bempp"}, 1, 5),
        ({}, {"engine": "bempp", "mesh_validation_mode": "off"}, 1, 5),
        ({}, {"engine": "beat-cpu"}, 1, 5),
    ],
)
def test_preview_builds_the_solve_execution_geometry_and_reuses_its_cache(
    monkeypatch, design_extra, options, quadrants, wall,
) -> None:
    seen = []
    stub = _stub_worker([])

    async def worker(fn, design_dump, **kwargs):
        seen.append(DesignConfig.model_validate(design_dump))
        return await stub(fn, design_dump, **kwargs)

    registry = EngineRegistry(
        detector=lambda: [EngineInfo(
            "bempp", True, "test", "test",
            mountings=("free-standing", "infinite-baffle", "ground-plane"),
            ground_plane_axes=("x", "y", "z"),
        )],
        factory=lambda name: object(),
    )
    monkeypatch.setattr(mesh_builder, "run_mesh_build", worker)
    clear_solver_mesh_cache()

    async def scenario():
        request = SolveRequest.model_validate({"design": _rosse(**design_extra), "options": options})
        resolved = await resolve_submission(request, registry)
        execution = _execution_request(resolved.request, resolved_quadrants=quadrants)
        preview = await solver_mesh_response(
            request.design, None, options=request.options, engine_registry=registry,
        )
        assert preview["quadrants"] == resolved.symmetry_metadata["resolved_quadrants"] == quadrants
        assert seen == [execution.design]
        thickness = seen[0].root.mesh.wall_thickness
        assert (thickness.constant_value() if thickness is not None else None) == wall
        assert preview["plan"]["engine"] == resolved.engine_name
        assert preview["plan"]["adjustments"] == resolved.symmetry_metadata["solver_plan"].get("adjustments", [])
        # The actual solve adapter calls this builder with these execution inputs.
        solved = await mesh_builder.build_solver_mesh(execution.design, execution.options)
        assert solved["stats"]["mesh_cache_hit"] is True
        assert solved["stats"]["mesh_cache_key"] == preview["stats"]["mesh_cache_key"]
        assert len(seen) == 1
        assert request.design == _rosse(**design_extra)  # no mutation of the source

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()


@pytest.mark.parametrize("engine", ["auto", "bempp", "dryrun", "beat-cpu"])
@pytest.mark.parametrize("accuracy", ["fast", "accurate"])
def test_unavailable_engine_refuses_solve_but_keeps_preview(monkeypatch, engine, accuracy):
    registry = EngineRegistry(detector=lambda: [])
    monkeypatch.setattr(mesh_builder, "run_mesh_build", _stub_worker([]))
    clear_solver_mesh_cache()

    async def scenario():
        request = SolveRequest.model_validate({
            "design": _rosse(), "options": {"engine": engine, "accuracy": accuracy},
        })
        with pytest.raises(EngineUnavailableError):
            await resolve_submission(request, registry)
        preview = await solver_mesh_response(
            request.design, None, options=request.options, engine_registry=registry,
        )
        assert preview["quadrants"] == 1
        assert preview["msh_text"] == STUB_MSH
        if engine == "bempp":
            assert preview["plan"]["adjustments"][0]["effective_mm"] == 5

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()


def test_unavailable_bempp_still_applies_infinite_baffle_domain(monkeypatch):
    monkeypatch.setattr(mesh_builder, "run_mesh_build", _stub_worker([]))
    clear_solver_mesh_cache()
    try:
        preview = asyncio.run(solver_mesh_response(
            _rosse(simulation={"sim_type": "infinite-baffle"}), None,
            options=SolveOptions(engine="bempp"),
            engine_registry=EngineRegistry(detector=lambda: []),
        ))
        assert preview["quadrants"] == 1234
        assert preview["cut_planes"] == []
        assert preview["plan"]["adjustments"] == []
    finally:
        clear_solver_mesh_cache()


def test_legacy_body_and_nested_solve_options_reach_the_route(monkeypatch):
    monkeypatch.setattr(mesh_builder, "run_mesh_build", _stub_worker([]))
    clear_solver_mesh_cache()
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    try:
        with TestClient(app) as client:
            design = _rosse().model_dump(mode="json")
            legacy = client.post("/api/solver-mesh", json={"design": design, "symmetry": "full"})
            assert legacy.status_code == 200
            assert legacy.json()["quadrants"] == 1234
            default = client.post("/api/solver-mesh", json={"design": design})
            assert default.status_code == 200
            assert default.json()["quadrants"] == 1
            nested = client.post("/api/solver-mesh", json={
                "design": design, "options": {"engine": "bempp", "symmetry": "half_yz"},
            })
            assert nested.status_code == 200
            assert nested.json()["quadrants"] == 14
            assert nested.json()["plan"]["adjustments"][0]["effective_mm"] == 5
    finally:
        clear_solver_mesh_cache()


def test_real_bempp_preview_contains_the_defaulted_wall_mesh():
    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    clear_solver_mesh_cache()
    registry = EngineRegistry(detector=lambda: [])

    async def scenario():
        defaulted = await solver_mesh_response(
            _rosse(mesh={"wall_thickness": 0}), None,
            options=SolveOptions(engine="bempp"), engine_registry=registry,
        )
        explicit = await solver_mesh_response(
            _rosse(mesh={"wall_thickness": 5}), None,
            options=SolveOptions(engine="bempp"), engine_registry=registry,
        )
        bare = await solver_mesh_response(
            _rosse(mesh={"wall_thickness": 0}), None,
            options=SolveOptions(), engine_registry=registry,
        )
        assert defaulted["msh_text"] == explicit["msh_text"]
        assert explicit["stats"]["mesh_cache_hit"] is True
        assert defaulted["stats"]["mesh_cache_key"] == explicit["stats"]["mesh_cache_key"]
        assert defaulted["stats"]["mesh_cache_key"] != bare["stats"]["mesh_cache_key"]
        # A closed wall contributes outer and rim triangles absent from the shell.
        assert defaulted["stats"]["triangle_count"] > bare["stats"]["triangle_count"]
        assert defaulted["plan"]["adjustments"][0]["effective_mm"] == 5
        assert explicit["plan"]["adjustments"] == []

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()


def test_unavailable_auto_engine_still_resolves_floor_domain(monkeypatch):
    monkeypatch.setattr(mesh_builder, "run_mesh_build", _stub_worker([]))
    clear_solver_mesh_cache()
    try:
        preview = asyncio.run(solver_mesh_response(
            _rosse(), None,
            options=SolveOptions(ground_plane={"enabled": True, "axis": "y"}),
            engine_registry=EngineRegistry(detector=lambda: []),
        ))
        assert preview["quadrants"] == 14
        assert preview["cut_planes"] == ["x0"]
    finally:
        clear_solver_mesh_cache()
