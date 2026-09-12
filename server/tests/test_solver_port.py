"""The shared HornLab-facing BEAT/Metal contact points."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from server.solver import beat, metal
from server.solver.base import (
    EngineRunResult,
    is_full3d_solver_port,
    run_full3d_solver_port,
)
from server.solver.context import SolverContext
from server.solver.ground_plane import GroundPlane


def _context(**overrides) -> SolverContext:
    values = {
        "design": None,
        "frequency_range": (500.0, 2000.0),
        "num_frequencies": 3,
    }
    values.update(overrides)
    return SolverContext(**values)


def test_beat_and_metal_expose_the_same_run_contact_points() -> None:
    beat_params = inspect.signature(beat.BeatEngine.run).parameters
    metal_params = inspect.signature(metal.MetalEngine.run).parameters
    assert tuple(beat_params) == tuple(metal_params)
    assert {"cancel_cb", "stage_cb", "artifact_cb", "result_cb", "imported_record"} <= set(
        beat_params
    )
    assert is_full3d_solver_port(beat.BeatEngine("cpu"))
    assert is_full3d_solver_port(metal.MetalEngine())
    # Naming alone must not opt an unrelated adapter into the port.
    assert not is_full3d_solver_port(SimpleNamespace(name="beat-experimental"))


def test_port_forwards_lifecycle_callbacks_and_normalized_result() -> None:
    calls = []

    class Port:
        name = "metal"

        async def run(self, request, **kwargs):
            assert request is marker
            assert kwargs["imported_record"] == {"mesh": "verified"}
            kwargs["cancel_cb"]()
            kwargs["stage_cb"]("frequency_solve", 0.5, "halfway")
            kwargs["result_cb"](0, {"frequency_hz": 500.0})
            await kwargs["artifact_cb"]("$MeshFormat", {"triangle_count": 1})
            return EngineRunResult(results={"metadata": {"solver_backend": "metal"}})

    async def artifact(msh, stats):
        calls.append(("artifact", msh, stats))

    marker = object()
    result = asyncio.run(
        run_full3d_solver_port(
            Port(),
            marker,
            cancel_cb=lambda: calls.append(("cancel",)),
            stage_cb=lambda *args: calls.append(("stage", *args)),
            result_cb=lambda *args: calls.append(("result", *args)),
            artifact_cb=artifact,
            imported_record={"mesh": "verified"},
        )
    )
    assert result.results["metadata"]["solver_backend"] == "metal"
    assert calls == [
        ("cancel",),
        ("stage", "frequency_solve", 0.5, "halfway"),
        ("result", 0, {"frequency_hz": 500.0}),
        ("artifact", "$MeshFormat", {"triangle_count": 1}),
    ]


def test_port_refuses_non_normalized_result() -> None:
    class Port:
        name = "beat-cpu"

        async def run(self, request, **kwargs):
            return {"results": {}}

    with pytest.raises(TypeError, match="normalized EngineRunResult"):
        asyncio.run(
            run_full3d_solver_port(
                Port(), object(), cancel_cb=lambda: None, stage_cb=lambda *_: None
            )
        )


def test_beat_port_refuses_imported_record_before_meshing() -> None:
    request = SimpleNamespace(
        geometry=None,
        options=SimpleNamespace(ground_plane=SimpleNamespace(enabled=False)),
    )
    with pytest.raises(beat.BeatUnavailable, match="imported geometry"):
        asyncio.run(
            beat.BeatEngine("cpu").run(
                request,
                cancel_cb=lambda: None,
                stage_cb=lambda *_: None,
                imported_record={"mesh": "verified"},
            )
        )


@pytest.mark.parametrize(
    ("solve", "error"),
    [
        (beat.solve_beat_from_msh_text, beat.BeatUnavailable),
        (metal.solve_metal_from_msh_text, metal.MetalUnavailable),
    ],
)
def test_bem_port_refuses_ground_before_native_solve(solve, error) -> None:
    context = _context(ground_plane=GroundPlane(axis="y", height_m=1.0))
    with pytest.raises(error, match="rigid ground plane"):
        solve("$MeshFormat\n", context)


@pytest.mark.parametrize(
    ("engine", "error"),
    [
        (beat.BeatEngine("cpu"), beat.BeatUnavailable),
        (metal.MetalEngine(), metal.MetalUnavailable),
    ],
)
def test_bem_port_refuses_ground_before_meshing_or_artifact(engine, error) -> None:
    request = SimpleNamespace(
        geometry=None,
        options=SimpleNamespace(ground_plane=SimpleNamespace(enabled=True)),
    )
    calls = []

    async def artifact(*args):
        calls.append(("artifact", *args))

    with pytest.raises(error, match="rigid ground plane"):
        asyncio.run(
            engine.run(
                request,
                cancel_cb=lambda: calls.append(("cancel",)),
                stage_cb=lambda *args: calls.append(("stage", *args)),
                artifact_cb=artifact,
            )
        )
    assert calls == []
