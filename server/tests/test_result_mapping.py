"""Regression tests for solver result reference and spherical-cell reporting."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from server.design.schema import DesignConfig
from server.jobs.models import PolarConfig
from server.solver.context import SolverContext
from server.solver.result_mapping import build_solver_response


def _context(*, spherical: bool = False) -> SolverContext:
    context = SolverContext(
        design=DesignConfig.model_validate({"formula": "OSSE"}),
        frequency_range=(100.0, 200.0),
        num_frequencies=2,
    )
    context.polar_config["spherical_sampling"] = spherical
    return context


def _config(*, sphere_grid: tuple[int, int] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        observation=SimpleNamespace(
            distance_m=2.0,
            origin="mouth",
            sphere_grid=sphere_grid,
        )
    )


def _result(*, angles: list[float]) -> SimpleNamespace:
    frequencies = np.asarray([100.0, 200.0])
    pressure = np.ones((2, 1, len(angles)), dtype=np.complex128) * 20.0e-6
    return SimpleNamespace(
        frequencies_hz=frequencies,
        observation_angles_deg=np.asarray(angles, dtype=float),
        observation_planes=["horizontal"],
        pressure_complex=pressure,
        directivity_db=np.zeros(pressure.shape, dtype=float),
        impedance=np.zeros(frequencies.size, dtype=np.complex128),
    )


def test_polar_normalization_angle_must_be_inside_the_sampled_domain() -> None:
    for norm_angle in (9.0, 71.0):
        with pytest.raises(ValidationError, match="norm_angle.*angle_range"):
            PolarConfig(angle_range=(10.0, 70.0, 7), norm_angle=norm_angle)

    # Off-axis-only grids remain legitimate when their normalization reference
    # is also within the sampled domain.
    polar = PolarConfig(angle_range=(10.0, 70.0, 7), norm_angle=15.0)
    assert polar.angle_range == (10.0, 70.0, 7)


def test_off_axis_grid_labels_the_sample_used_for_spl_and_phase() -> None:
    result = _result(angles=[10.0, 20.0])
    result.pressure_complex[:, 0, 0] = np.asarray([40.0e-6j, -40.0e-6j])
    context = _context()
    context.polar_config.update(
        {"angle_range": (10.0, 20.0, 2), "norm_angle": 10.0}
    )

    response = build_solver_response(
        result=result,
        config=_config(),
        context=context,
        start_time=0.0,
        metadata={},
        sound_speed_m_per_s=343.0,
    )

    assert response["spl_on_axis"]["spl"] == pytest.approx([6.0206, 6.0206])
    assert response["spl_on_axis"]["phase_degrees"] == pytest.approx([90.0, -90.0])
    assert response["metadata"]["spl_on_axis"] == {
        "requested_angle_degrees": 0.0,
        "sampled_angle_degrees": 10.0,
    }
    assert response["metadata"]["warning_count"] == 1
    assert "10 degrees" in response["metadata"]["warnings"][0]


def test_mesh_resolution_suspects_aggregate_into_one_warning() -> None:
    result = _result(angles=[0.0, 90.0])
    context = _context()
    context.polar_config.update({"angle_range": (0.0, 90.0, 2), "norm_angle": 0.0})
    metadata = {
        "metal": {
            "solver_log": [
                {
                    "frequency_hz": 100.0,
                    "native_diagnostics": {
                        "mesh_resolution_suspect": False,
                        "mesh_max_valid_frequency_hz": 150.0,
                        "mesh_max_edge_m": 0.05,
                        "mesh_elements_per_wavelength_min": 6.0,
                    },
                },
                {
                    "frequency_hz": 200.0,
                    "native_diagnostics": {
                        "dense_solve_suspect": True,
                        "mesh_resolution_suspect": True,
                        "mesh_max_valid_frequency_hz": 150.0,
                        "mesh_max_edge_m": 0.05,
                        "mesh_elements_per_wavelength_min": 6.0,
                    },
                },
            ]
        }
    }

    response = build_solver_response(
        result=result,
        config=_config(),
        context=context,
        start_time=0.0,
        metadata=metadata,
        sound_speed_m_per_s=343.0,
    )

    warnings = response["metadata"]["warnings"]
    mesh_warnings = [w for w in warnings if "Mesh resolution" in w]
    assert len(mesh_warnings) == 1
    assert "up to about 150 Hz" in mesh_warnings[0]
    assert "6 elements per wavelength" in mesh_warnings[0]
    assert "50.0 mm edge" in mesh_warnings[0]
    assert "1 of 2 solved frequencies (200-200 Hz)" in mesh_warnings[0]
    assert "Refine the mesh's mm resolutions" in mesh_warnings[0]
    # The per-frequency conditioning warning still stands on its own.
    assert any("Dense-solve conditioning is suspect at 200.0 Hz" in w for w in warnings)
    assert response["metadata"]["warning_count"] == len(warnings)
    # Accuracy suspects do not degrade the run to a partial success.
    assert response["metadata"]["partial_success"] is False


def test_mesh_resolution_all_clear_adds_no_warning() -> None:
    result = _result(angles=[0.0, 90.0])
    context = _context()
    context.polar_config.update({"angle_range": (0.0, 90.0, 2), "norm_angle": 0.0})
    metadata = {
        "metal": {
            "solver_log": [
                {
                    "frequency_hz": frequency,
                    "native_diagnostics": {
                        "mesh_resolution_suspect": False,
                        "mesh_max_valid_frequency_hz": 1500.0,
                    },
                }
                for frequency in (100.0, 200.0)
            ]
        }
    }

    response = build_solver_response(
        result=result,
        config=_config(),
        context=context,
        start_time=0.0,
        metadata=metadata,
        sound_speed_m_per_s=343.0,
    )

    assert not any("Mesh resolution" in w for w in response["metadata"]["warnings"])
    assert response["metadata"]["warning_count"] == len(
        response["metadata"]["warnings"]
    )


def test_mesh_resolution_warning_survives_missing_policy_fields() -> None:
    # An older solver pin may flag the suspect without the companion numbers;
    # the aggregate must still name the problem instead of crashing or hiding.
    result = _result(angles=[0.0, 90.0])
    context = _context()
    context.polar_config.update({"angle_range": (0.0, 90.0, 2), "norm_angle": 0.0})
    metadata = {
        "metal": {
            "solver_log": [
                {"frequency_hz": 100.0, "native_diagnostics": {}},
                {
                    "frequency_hz": float("nan"),
                    "native_diagnostics": {"mesh_resolution_suspect": True},
                },
            ]
        }
    }

    response = build_solver_response(
        result=result,
        config=_config(),
        context=context,
        start_time=0.0,
        metadata=metadata,
        sound_speed_m_per_s=343.0,
    )

    mesh_warnings = [
        w for w in response["metadata"]["warnings"] if "Mesh resolution" in w
    ]
    assert len(mesh_warnings) == 1
    assert "insufficient for part of this sweep" in mesh_warnings[0]
    assert "1 solved frequency" in mesh_warnings[0]


def test_nonfinite_sphere_pressure_nulls_only_its_frequency() -> None:
    result = _result(angles=[0.0, 90.0])
    result.sphere_theta_deg = np.asarray([0, 0, 0, 90, 90, 90], dtype=float)
    result.sphere_phi_deg = np.asarray([0, 120, 240, 0, 120, 240], dtype=float)
    result.sphere_pressure_complex = np.ones((2, 6), dtype=np.complex128) * 20.0e-6
    result.sphere_pressure_complex[0, 4] = complex(np.nan, 0.0)
    context = _context(spherical=True)
    context.sim_type = 1

    response = build_solver_response(
        result=result,
        config=_config(sphere_grid=(2, 3)),
        context=context,
        start_time=0.0,
        metadata={},
        sound_speed_m_per_s=343.0,
    )

    assert response["metadata"]["balloon_sampling"]["status"] == "available"
    assert response["balloon"]["spl_norm_db"][0] == [[None] * 3, [None] * 3]
    assert response["balloon"]["spl_norm_db"][1] == [[0.0] * 3, [0.0] * 3]
    assert response["di"]["di"][0] is None
    assert response["di"]["di"][1] == pytest.approx(3.0103, abs=1.0e-4)
    assert response["metadata"]["partial_success"] is True
    assert response["metadata"]["failure_count"] == 1
    assert response["metadata"]["failures"][0]["frequency_hz"] == 100.0
    assert response["metadata"]["failures"][0]["code"] == (
        "nonfinite_spherical_pressure"
    )
