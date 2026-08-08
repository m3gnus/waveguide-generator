"""Differential tests for the vectorized result postprocessing paths."""

from __future__ import annotations

import enum
import math
from types import SimpleNamespace
from typing import Any

import numpy as np

from server.solver import beam_shape
from server.solver.result_mapping import directivity, json_safe_native_value


def _scalar_json_safe(value: Any) -> Any:
    if isinstance(value, complex):
        return {
            "real": float(value.real) if math.isfinite(float(value.real)) else None,
            "imaginary": (
                float(value.imag) if math.isfinite(float(value.imag)) else None
            ),
        }
    if isinstance(value, enum.Enum):
        return _scalar_json_safe(value.value)
    if isinstance(value, np.generic):
        return _scalar_json_safe(value.item())
    if isinstance(value, np.ndarray):
        return _scalar_json_safe(value.tolist())
    if isinstance(value, dict):
        return {str(key): _scalar_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scalar_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _scalar_directivity(result: Any) -> dict[str, list[list[list[float | None]]]]:
    try:
        angles = np.asarray(result.observation_angles_deg, dtype=float)
        values = np.asarray(result.directivity_db, dtype=float)
        frequency_count = len(np.asarray(result.frequencies_hz).reshape(-1))
    except (TypeError, ValueError):
        return {}
    if angles.ndim != 1 or values.ndim != 3:
        return {}
    finite_angle_indices = [
        index for index, angle in enumerate(angles) if math.isfinite(float(angle))
    ]
    output: dict[str, list[list[list[float | None]]]] = {}
    for plane_index, plane_name in enumerate(result.observation_planes):
        if plane_index >= values.shape[1]:
            break
        plane_rows: list[list[list[float | None]]] = []
        for frequency_index in range(frequency_count):
            row: list[list[float | None]] = []
            for angle_index in finite_angle_indices:
                if angle_index >= values.shape[2]:
                    continue
                level = None
                if frequency_index < values.shape[0]:
                    number = float(values[frequency_index, plane_index, angle_index])
                    level = number if math.isfinite(number) else None
                row.append([float(angles[angle_index]), level])
            plane_rows.append(row)
        output[str(plane_name)] = plane_rows
    return output


def _scalar_fit_frequency(
    theta_grid_deg: np.ndarray,
    phi_grid_deg: np.ndarray,
    spl_grid_db: np.ndarray,
    *,
    level_db: float,
) -> dict[str, float] | None:
    theta_samples = np.linspace(
        0.0,
        min(beam_shape.MAX_FRONT_ANGLE_DEG, float(theta_grid_deg[-1])),
        beam_shape.RAY_SAMPLES,
    )
    psi_deg = np.arange(beam_shape.RAY_COUNT, dtype=np.float64) * (
        360.0 / beam_shape.RAY_COUNT
    )
    crossings: dict[int, float] = {}
    for index, psi in enumerate(psi_deg):
        ray = beam_shape._interp_meridian(
            theta_grid_deg,
            phi_grid_deg,
            spl_grid_db,
            psi,
            theta_samples,
        )
        crossing = beam_shape._ray_crossing_radius(theta_samples, ray, level_db)
        if crossing is not None:
            crossings[index] = crossing
    if len(crossings) < max(24, beam_shape.RAY_COUNT // 4):
        return None

    indices = np.asarray(sorted(crossings), dtype=int)
    psi_rad = np.deg2rad(psi_deg[indices])
    radii = np.asarray([crossings[int(index)] for index in indices])
    xs, ys = radii * np.cos(psi_rad), radii * np.sin(psi_rad)
    horizontal, vertical = beam_shape._axis_extents(psi_deg, crossings, xs, ys)
    if horizontal is None or vertical is None:
        return None
    a = 0.5 * (horizontal[0] + horizontal[1])
    b = 0.5 * (vertical[0] + vertical[1])
    if not (math.isfinite(a) and math.isfinite(b)) or a <= 1.0e-6 or b <= 1.0e-6:
        return None
    exponent, rms = beam_shape._fit_exponent(psi_rad, radii, a, b)
    h_bw = math.degrees(math.atan(horizontal[0]) + math.atan(horizontal[1]))
    v_bw = math.degrees(math.atan(vertical[0]) + math.atan(vertical[1]))
    return {
        "shape_exponent": float(exponent),
        "fit_residual_percent": float(100.0 * rms / max((a + b) / 2.0, 1.0e-6)),
        "horizontal_beamwidth_deg": float(h_bw),
        "vertical_beamwidth_deg": float(v_bw),
        "aspect_ratio": float(h_bw / max(v_bw, 1.0e-6)),
    }


def _scalar_beam_shape_summary(
    theta: np.ndarray,
    phi: np.ndarray,
    spl: np.ndarray,
    frequencies: np.ndarray,
) -> dict[str, Any]:
    fields: dict[str, list[float | None]] = {
        "shape_exponent": [],
        "fit_residual_percent": [],
        "horizontal_beamwidth_deg": [],
        "vertical_beamwidth_deg": [],
        "aspect_ratio": [],
        "spherical_di_db": [],
    }
    valid: list[bool] = []
    for grid in spl:
        wrapped_phi, wrapped_grid = beam_shape._wrap_phi_columns(phi, grid)
        fit = _scalar_fit_frequency(
            theta, wrapped_phi, wrapped_grid, level_db=beam_shape.BEAM_LEVEL_DB
        )
        if fit is None:
            for key in list(fields)[:5]:
                fields[key].append(None)
            valid.append(False)
        else:
            fields["shape_exponent"].append(round(fit["shape_exponent"], 3))
            fields["fit_residual_percent"].append(
                round(fit["fit_residual_percent"], 2)
            )
            fields["horizontal_beamwidth_deg"].append(
                round(fit["horizontal_beamwidth_deg"], 2)
            )
            fields["vertical_beamwidth_deg"].append(
                round(fit["vertical_beamwidth_deg"], 2)
            )
            fields["aspect_ratio"].append(round(fit["aspect_ratio"], 3))
            valid.append(True)
        di = beam_shape._spherical_di_db(theta, grid)
        fields["spherical_di_db"].append(None if di is None else round(di, 2))
    return {
        "frequencies": [float(value) for value in frequencies],
        **fields,
        "level_db": beam_shape.BEAM_LEVEL_DB,
        "di_domain": "sphere",
        "valid": valid,
    }


def test_numeric_array_json_sanitizer_matches_recursive_reference() -> None:
    arrays = [
        np.arange(60, dtype=np.int32).reshape(3, 4, 5),
        np.asarray([True, False], dtype=bool),
        np.asarray([[1.25, np.nan], [np.inf, -np.inf]], dtype=np.float64),
        np.asarray(3.5),
        np.asarray(np.nan),
        np.asarray([1.0 + 2.0j, complex(np.inf, np.nan)]),
    ]
    for values in arrays:
        assert json_safe_native_value(values) == _scalar_json_safe(values)


def test_directivity_vectorization_matches_scalar_reference() -> None:
    rng = np.random.default_rng(20260807)
    values = rng.normal(size=(7, 2, 11))
    values[1, 0, 3] = np.nan
    values[4, 1, 6] = np.inf
    result = SimpleNamespace(
        frequencies_hz=np.geomspace(100.0, 1000.0, 9),
        observation_angles_deg=np.asarray(
            [-90.0, -72.0, np.nan, -36.0, -18.0, 0.0, 18.0, 36.0, 54.0, 72.0, 90.0, 108.0]
        ),
        observation_planes=["horizontal", "vertical", "unused"],
        directivity_db=values,
    )
    assert directivity(result) == _scalar_directivity(result)


def test_beam_shape_vectorization_matches_scalar_contract_output() -> None:
    theta = np.linspace(0.0, 89.0, 37)
    phi = np.arange(0.0, 360.0, 5.0)
    frequencies = np.geomspace(100.0, 1600.0, 9)
    theta_rad = np.deg2rad(theta)[:, None]
    phi_rad = np.deg2rad(phi)[None, :]
    grids = np.stack(
        [
            -26.0
            * (theta_rad / np.deg2rad(89.0)) ** (1.7 + 0.08 * index)
            * (1.0 + 0.24 * np.cos(2.0 * phi_rad + 0.07 * index))
            for index in range(frequencies.size)
        ]
    )
    grids[3, 12, 17] = np.nan
    expected = _scalar_beam_shape_summary(theta, phi, grids, frequencies)
    assert beam_shape.beam_shape_summary(theta, phi, grids, frequencies) == expected
