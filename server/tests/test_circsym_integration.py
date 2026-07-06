from __future__ import annotations

import math

import pytest

from contracts import MeshData, PolarConfig, SimulationRequest
from solver.metal_solver import metal_backend_status, solve_circsym_from_params


def _metal_runtime_ready() -> bool:
    try:
        status = metal_backend_status()
    except Exception:
        return False
    if not status.get("available"):
        return False
    try:
        from hornlab_metal_bem.sweep import _discover_runtime_smoke_cached
    except Exception:
        return False
    try:
        runtime = _discover_runtime_smoke_cached()
    except Exception:
        return False
    return bool(getattr(runtime, "available", False))


def _payload(*, sim_type: int) -> dict:
    return {
        "formula_type": "OSSE",
        "L": 40.0,
        "r0": 8.0,
        "a": 25.0,
        "a0": 8.0,
        "k": 1.0,
        "n": 4.0,
        "q": 0.99,
        "s": 0.0,
        "n_angular": 24,
        "n_length": 8,
        "quadrants": 1234,
        "throat_res": 8.0,
        "mouth_res": 20.0,
        "rear_res": 24.0,
        "wall_thickness": 0.0 if sim_type == 1 else 4.0,
        "enc_depth": 0.0,
        "source_shape": 2,
        "source_velocity": 1,
        "sim_type": sim_type,
    }


def _request(payload: dict) -> SimulationRequest:
    return SimulationRequest(
        mesh=MeshData(
            vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            indices=[0, 1, 2],
            surfaceTags=[2],
            format="msh",
            boundaryConditions={},
            metadata={},
        ),
        frequency_range=[500.0, 500.0],
        num_frequencies=1,
        frequency_spacing="linear",
        sim_type=str(payload["sim_type"]),
        solver_mode="circsym",
        solver_backend="metal",
        polar_config=PolarConfig(
            angle_range=[0.0, 180.0, 5],
            enabled_axes=["horizontal"],
            distance=2.0,
            observation_origin="mouth",
        ),
        options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": dict(payload)}},
    )


@pytest.mark.skipif(not _metal_runtime_ready(), reason="Metal runtime not available")
@pytest.mark.parametrize("sim_type", [1, 2])
def test_solve_circsym_from_params_unmocked_tiny_round_waveguide(sim_type: int):
    payload = _payload(sim_type=sim_type)
    request = _request(payload)

    result = solve_circsym_from_params(payload, request)

    assert result["frequencies"] == [500.0]
    assert result["metadata"]["solver_mode"] == "circsym"
    assert result["metadata"]["metal"]["solver_mode"] == "circsym"
    assert result["metadata"]["metal"]["meridian"]["freqMaxHz"] == 500.0
    assert len(result["spl_on_axis"]["spl"]) == 1
    spl_on_axis = result["spl_on_axis"]["spl"][0]
    assert spl_on_axis is not None
    assert math.isfinite(float(spl_on_axis))
    assert math.isfinite(float(result["impedance"]["real"][0]))
    assert "horizontal" in result["directivity"]
    assert len(result["directivity"]["horizontal"][0]) == 5

    if sim_type == 1:
        native_diagnostics = result["metadata"]["metal"]["native_diagnostics"]
        diagnostic_entries = [
            entry for entry in native_diagnostics if isinstance(entry, dict)
        ]
        assert any(entry.get("coupled_ib") is True for entry in diagnostic_entries)
        assert any(
            int(entry.get("aperture_tag")) == 12
            for entry in diagnostic_entries
            if entry.get("aperture_tag") is not None
        )
