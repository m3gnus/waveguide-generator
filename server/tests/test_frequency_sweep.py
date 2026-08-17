from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np

from server.design.schema import DesignConfig
from server.mesh import builder as mesh_builder
from server.mesh.builder import build_solver_mesh, clear_solver_mesh_cache
from server.solver.combine import combine_drive_channels
from server.solver.context import SolverContext
from server.solver.frequency_sweep import (
    canonical_frequencies,
    live_execution_frequencies,
    order_frequencies_for_live_plotting,
    sort_native_result_frequencies,
)


def _context(*, spacing: str = "linear") -> SolverContext:
    return SolverContext(
        design=None,
        frequency_range=(100.0, 700.0),
        num_frequencies=7,
        frequency_spacing=spacing,
        polar_config={"enabled_axes": ["horizontal"]},
    )


def test_live_order_matches_boundary_lab_endpoint_then_vdc_scheduler() -> None:
    ordered = order_frequencies_for_live_plotting(range(1, 11))

    assert ordered.tolist() == [1.0, 10.0, 9.0, 5.0, 3.0, 7.0, 2.0, 6.0, 4.0, 8.0]
    assert sorted(ordered.tolist()) == [float(value) for value in range(1, 11)]


def test_context_grid_is_canonical_but_live_execution_is_progressive() -> None:
    context = _context()

    assert canonical_frequencies(context).tolist() == [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0]
    assert live_execution_frequencies(context).tolist() == [100.0, 700.0, 500.0, 300.0, 200.0, 600.0, 400.0]


def test_native_result_is_restored_to_ascending_frequency_contract() -> None:
    result = SimpleNamespace(
        frequencies_hz=np.asarray([100.0, 700.0, 500.0, 300.0]),
        pressure_complex=np.asarray([[1], [7], [5], [3]]),
        directivity_db=np.asarray([[10], [70], [50], [30]]),
        impedance=np.asarray([1, 7, 5, 3]),
        sphere_pressure_complex=np.asarray([[101], [107], [105], [103]]),
        surface_pressure_avg={2: np.asarray([201, 207, 205, 203])},
        solver_log=[{"frequency_hz": 100.0}, {"frequency_hz": 700.0}],
    )

    returned = sort_native_result_frequencies(result)

    assert returned is result
    assert result.frequencies_hz.tolist() == [100.0, 300.0, 500.0, 700.0]
    assert result.pressure_complex[:, 0].tolist() == [1, 3, 5, 7]
    assert result.directivity_db[:, 0].tolist() == [10, 30, 50, 70]
    assert result.impedance.tolist() == [1, 3, 5, 7]
    assert result.sphere_pressure_complex[:, 0].tolist() == [101, 103, 105, 107]
    assert result.surface_pressure_avg[2].tolist() == [201, 203, 205, 207]
    assert result.solver_log == [{"frequency_hz": 100.0}, {"frequency_hz": 700.0}]


def test_bempp_read_only_directivity_alias_sorts_its_backing_spl_field() -> None:
    class BemppShapedResult:
        def __init__(self) -> None:
            self.frequencies_hz = np.asarray([100.0, 700.0, 500.0, 300.0])
            self.pressure_complex = np.asarray([[1], [7], [5], [3]])
            self.spl_db = np.asarray([[10], [70], [50], [30]])
            self.impedance = np.asarray([1, 7, 5, 3])

        @property
        def directivity_db(self) -> np.ndarray:
            return self.spl_db

    result = BemppShapedResult()

    returned = sort_native_result_frequencies(result)

    assert returned is result
    assert result.frequencies_hz.tolist() == [100.0, 300.0, 500.0, 700.0]
    assert result.directivity_db[:, 0].tolist() == [10, 30, 50, 70]


def _combine_member(freqs: np.ndarray, angles: np.ndarray) -> SimpleNamespace:
    field = np.ones((freqs.size, 1, angles.size), dtype=np.complex128)
    return SimpleNamespace(
        frequencies_hz=freqs,
        observation_angles_deg=angles,
        observation_planes=["horizontal"],
        observation_points=None,
        pressure_complex=field,
        solver_log=[],
        sphere_pressure_complex=None,
        sphere_theta_deg=None,
        sphere_phi_deg=None,
        sphere_points=None,
    )


def test_combine_warns_for_off_axis_reference_and_out_of_band_crossover() -> None:
    freqs = np.asarray([500.0, 1_000.0, 20_000.0])
    angles = np.asarray([10.0, 40.0, 70.0])
    results = {
        "low": _combine_member(freqs, angles),
        "high": _combine_member(freqs, angles),
    }

    _combined, payload = combine_drive_channels(
        results,
        members=["low", "high"],
        crossovers_hz=[300.0],
    )

    assert payload["reference_angle_degrees"] == 10.0
    assert any(
        "nearest available angle, 10 degrees" in item
        for item in payload["warnings"]
    )
    assert any(
        "outside the solved band [500, 20000] Hz" in item
        for item in payload["warnings"]
    )


def test_quadrant_compatibility_fallback_warning_is_request_local(monkeypatch) -> None:
    calls = 0

    async def fake_worker(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "msh_text": "$MeshFormat\n",
            "stats": {"warnings": []},
            "integrity": {"valid": True},
            "metadata": {},
        }

    monkeypatch.setattr(mesh_builder, "run_on_gmsh_worker", fake_worker)
    valid = DesignConfig.model_validate(
        {"formula": "OSSE", "mesh": {"quadrants": 1}}
    )
    typo = DesignConfig.model_validate(
        {"formula": "OSSE", "mesh": {"quadrants": 9}}
    )
    clear_solver_mesh_cache()

    async def scenario() -> None:
        first = await build_solver_mesh(valid, {})
        warned = await build_solver_mesh(typo, {})
        valid_again = await build_solver_mesh(valid, {})

        assert first["stats"]["warnings"] == []
        assert warned["stats"]["mesh_cache_hit"] is True
        assert len(warned["stats"]["warnings"]) == 1
        assert "declared as '9'" in warned["stats"]["warnings"][0]
        assert "quarter-domain" in warned["stats"]["warnings"][0]
        assert valid_again["stats"]["warnings"] == []

    try:
        asyncio.run(scenario())
        assert calls == 1
    finally:
        clear_solver_mesh_cache()
