"""POST /api/solver-mesh: the viewport's window onto the real solver artifact."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from server.design.schema import DesignConfig
from server.mesh import builder as mesh_builder
from server.mesh.api import solver_mesh_response
from server.mesh.builder import clear_solver_mesh_cache


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
    monkeypatch.setattr(mesh_builder, "run_on_gmsh_worker", _stub_worker(calls))
    clear_solver_mesh_cache()

    async def scenario() -> None:
        first = await solver_mesh_response(_rosse(), "auto")
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

        second = await solver_mesh_response(_rosse(), "auto")
        assert second["stats"]["mesh_cache_key"] == first["stats"]["mesh_cache_key"]
        assert second["stats"]["mesh_cache_hit"] is True

        # Frequency edits act after mesh generation and must be cache hits.
        frequency_edit = await solver_mesh_response(
            _rosse(simulation={"f1": 500, "f2": 5_000, "num_frequencies": 20}),
            "auto",
        )
        assert (
            frequency_edit["stats"]["mesh_cache_key"]
            == first["stats"]["mesh_cache_key"]
        )
        assert frequency_edit["stats"]["mesh_cache_hit"] is True

        # A geometry edit is a different artifact.
        geometry_edit = await solver_mesh_response(_rosse(R=170), "auto")
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
    monkeypatch.setattr(mesh_builder, "run_on_gmsh_worker", _stub_worker(calls))
    clear_solver_mesh_cache()

    async def scenario() -> None:
        full = await solver_mesh_response(_rosse(), "full")
        assert full["quadrants"] == 1234
        assert full["cut_planes"] == []

        half = await solver_mesh_response(_rosse(), "half_xz")
        assert half["quadrants"] == 12
        assert half["cut_planes"] == ["y0"]

        # Requesting a mirror plane the geometry lacks is a 422, not a build.
        with pytest.raises(HTTPException) as refusal:
            await solver_mesh_response(
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
    async def worker(fn, design_dump, cancel_cb, *args: Any, **kwargs: Any):
        # Yield so the disconnect watcher task gets its first poll in, the way
        # a real build yields by running on the worker thread.
        await asyncio.sleep(0.01)
        cancel_cb()
        raise AssertionError("cancel checkpoint should have raised")

    monkeypatch.setattr(mesh_builder, "run_on_gmsh_worker", worker)
    clear_solver_mesh_cache()

    async def scenario() -> None:
        async def already_gone() -> bool:
            return True

        with pytest.raises(HTTPException) as refusal:
            await solver_mesh_response(_rosse(), "auto", already_gone)
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
        response = await solver_mesh_response(_rosse(), "auto")
        assert response["msh_text"].lstrip().startswith("$MeshFormat")
        assert response["stats"]["triangle_count"] > 0
        assert response["cut_planes"] == ["x0", "y0"]
        # The artifact entered the shared cache under the solve's own key.
        again = await solver_mesh_response(_rosse(), "auto")
        assert again["stats"]["mesh_cache_hit"] is True
        assert (
            again["stats"]["mesh_cache_key"] == response["stats"]["mesh_cache_key"]
        )

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()
