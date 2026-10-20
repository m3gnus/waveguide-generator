"""The shared HornLab-facing BEAT/Metal/BEMPP contact points."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from server.solver import beat, metal, bempp
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


def test_all_full3d_engines_expose_the_same_run_contact_points() -> None:
    beat_params = inspect.signature(beat.BeatEngine.run).parameters
    metal_params = inspect.signature(metal.MetalEngine.run).parameters
    assert tuple(beat_params) == tuple(metal_params) == tuple(inspect.signature(bempp.BemppEngine.run).parameters)
    assert {"cancel_cb", "stage_cb", "artifact_cb", "result_cb", "imported_record"} <= set(
        beat_params
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
