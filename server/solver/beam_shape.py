"""Forward -6 dB beam-shape analysis of spherical balloon grids.

This is the v1 algorithm from ``server/solver/beam_shape.py:27-312``:
144 tangent-plane rays, 181 samples, a bounded superellipse fit, H/V
beamwidths, and the same full-sphere DI used by the main result contract.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .directivity_index import calculate_di_from_spherical_grid


BEAM_LEVEL_DB = -6.0
MAX_FRONT_ANGLE_DEG = 89.0
RAY_COUNT = 144
RAY_SAMPLES = 181
EXPONENT_BOUNDS = (0.75, 8.0)
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0


def _as_float_array(values: Any) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


def _wrap_phi_columns(phi_deg: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.concatenate([phi_deg, [phi_deg[0] + 360.0]]),
        np.concatenate([values, values[:, :1]], axis=1),
    )


def _interp_meridian(
    theta_grid_deg: np.ndarray,
    phi_grid_deg: np.ndarray,
    spl_grid_db: np.ndarray,
    phi_query_deg: float,
    theta_query_deg: np.ndarray,
) -> np.ndarray:
    phi = float(phi_query_deg) % 360.0
    index = int(np.searchsorted(phi_grid_deg, phi, side="right") - 1)
    index = max(0, min(index, phi_grid_deg.size - 2))
    span = phi_grid_deg[index + 1] - phi_grid_deg[index]
    weight = 0.0 if span <= 0 else (phi - phi_grid_deg[index]) / span
    column = (1.0 - weight) * spl_grid_db[:, index] + weight * spl_grid_db[:, index + 1]
    return np.interp(theta_query_deg, theta_grid_deg, column)


def _ray_crossing_radius(
    theta_samples_deg: np.ndarray,
    spl_along_ray_db: np.ndarray,
    level_db: float,
) -> float | None:
    if not np.all(np.isfinite(spl_along_ray_db)):
        return None
    below = spl_along_ray_db <= level_db
    if not np.any(below):
        return None
    index = int(np.argmax(below))
    if index == 0:
        return None
    radii = np.tan(np.deg2rad(theta_samples_deg))
    first, second = float(spl_along_ray_db[index - 1]), float(spl_along_ray_db[index])
    if first == second:
        return float(radii[index])
    fraction = (first - level_db) / (first - second)
    return float(radii[index - 1] + (radii[index] - radii[index - 1]) * fraction)


def _ray_crossings(
    theta_grid_deg: np.ndarray,
    phi_grid_deg: np.ndarray,
    spl_grid_db: np.ndarray,
    psi_deg: np.ndarray,
    theta_samples_deg: np.ndarray,
    level_db: float,
) -> dict[int, float]:
    """Interpolate and find every ray crossing in one array operation."""

    if not (
        np.all(np.diff(theta_grid_deg) > 0.0)
        and np.all(np.diff(phi_grid_deg) > 0.0)
    ):
        crossings: dict[int, float] = {}
        for index, psi in enumerate(psi_deg):
            ray = _interp_meridian(
                theta_grid_deg,
                phi_grid_deg,
                spl_grid_db,
                psi,
                theta_samples_deg,
            )
            crossing = _ray_crossing_radius(theta_samples_deg, ray, level_db)
            if crossing is not None:
                crossings[index] = crossing
        return crossings

    phi_query = np.mod(psi_deg, 360.0)
    phi_indices = np.searchsorted(phi_grid_deg, phi_query, side="right") - 1
    phi_indices = np.clip(phi_indices, 0, phi_grid_deg.size - 2)
    phi_spans = phi_grid_deg[phi_indices + 1] - phi_grid_deg[phi_indices]
    phi_weights = np.divide(
        phi_query - phi_grid_deg[phi_indices],
        phi_spans,
        out=np.zeros_like(phi_query),
        where=phi_spans > 0.0,
    )
    columns = (
        (1.0 - phi_weights)[None, :] * spl_grid_db[:, phi_indices]
        + phi_weights[None, :] * spl_grid_db[:, phi_indices + 1]
    )

    theta_indices = np.searchsorted(theta_grid_deg, theta_samples_deg, side="right") - 1
    theta_indices = np.clip(theta_indices, 0, theta_grid_deg.size - 2)
    theta_spans = theta_grid_deg[theta_indices + 1] - theta_grid_deg[theta_indices]
    theta_weights = np.divide(
        theta_samples_deg - theta_grid_deg[theta_indices],
        theta_spans,
        out=np.zeros_like(theta_samples_deg),
        where=theta_spans > 0.0,
    )
    theta_weights = np.where(theta_samples_deg <= theta_grid_deg[0], 0.0, theta_weights)
    theta_weights = np.where(theta_samples_deg >= theta_grid_deg[-1], 1.0, theta_weights)
    rays = (
        (1.0 - theta_weights)[:, None] * columns[theta_indices]
        + theta_weights[:, None] * columns[theta_indices + 1]
    )

    below = rays <= level_db
    first_indices = np.argmax(below, axis=0)
    valid = np.all(np.isfinite(rays), axis=0) & np.any(below, axis=0) & (first_indices > 0)
    ray_indices = np.flatnonzero(valid)
    if ray_indices.size == 0:
        return {}
    first_indices = first_indices[ray_indices]
    previous_values = rays[first_indices - 1, ray_indices]
    crossing_values = rays[first_indices, ray_indices]
    radii = np.tan(np.deg2rad(theta_samples_deg))
    fractions = np.divide(
        previous_values - level_db,
        previous_values - crossing_values,
        out=np.ones_like(previous_values),
        where=previous_values != crossing_values,
    )
    crossing_radii = radii[first_indices - 1] + (
        radii[first_indices] - radii[first_indices - 1]
    ) * fractions
    return {
        int(index): float(radius)
        for index, radius in zip(ray_indices, crossing_radii, strict=True)
    }


def _superellipse_radius(
    psi_rad: np.ndarray, a: float, b: float, exponent: float
) -> np.ndarray:
    term = np.abs(np.cos(psi_rad) / a) ** exponent + np.abs(np.sin(psi_rad) / b) ** exponent
    return term ** (-1.0 / exponent)


def _fit_exponent(
    psi_rad: np.ndarray, radii: np.ndarray, a: float, b: float
) -> tuple[float, float]:
    def objective(exponent: float) -> float:
        return float(np.mean((radii - _superellipse_radius(psi_rad, a, b, exponent)) ** 2))

    low, high = EXPONENT_BOUNDS
    x1 = high - _GOLDEN * (high - low)
    x2 = low + _GOLDEN * (high - low)
    f1, f2 = objective(x1), objective(x2)
    for _ in range(48):
        if f1 <= f2:
            high, x2, f2 = x2, x1, f1
            x1 = high - _GOLDEN * (high - low)
            f1 = objective(x1)
        else:
            low, x1, f1 = x1, x2, f2
            x2 = low + _GOLDEN * (high - low)
            f2 = objective(x2)
    exponent = (low + high) / 2.0
    return exponent, math.sqrt(objective(exponent))


def _signed_extent(values: np.ndarray) -> tuple[float, float] | None:
    positive, negative = values[values > 0.0], values[values < 0.0]
    if positive.size == 0 or negative.size == 0:
        return None
    return float(np.max(positive)), float(-np.min(negative))


def _axis_extents(
    psi_deg: np.ndarray,
    crossings: dict[int, float],
    xs: np.ndarray,
    ys: np.ndarray,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    def radius(angle: float) -> float | None:
        matches = np.where(np.isclose(psi_deg, angle))[0]
        return crossings.get(int(matches[0])) if matches.size else None

    h_pos, h_neg, v_pos, v_neg = radius(0.0), radius(180.0), radius(90.0), radius(270.0)
    horizontal = (h_pos, h_neg) if h_pos is not None and h_neg is not None else _signed_extent(xs)
    vertical = (v_pos, v_neg) if v_pos is not None and v_neg is not None else _signed_extent(ys)
    return horizontal, vertical


def _fit_frequency(
    theta_grid_deg: np.ndarray,
    phi_grid_deg: np.ndarray,
    spl_grid_db: np.ndarray,
    *,
    level_db: float,
) -> dict[str, float] | None:
    theta_samples = np.linspace(
        0.0, min(MAX_FRONT_ANGLE_DEG, float(theta_grid_deg[-1])), RAY_SAMPLES
    )
    psi_deg = np.arange(RAY_COUNT, dtype=np.float64) * (360.0 / RAY_COUNT)
    crossings = _ray_crossings(
        theta_grid_deg,
        phi_grid_deg,
        spl_grid_db,
        psi_deg,
        theta_samples,
        level_db,
    )
    if len(crossings) < max(24, RAY_COUNT // 4):
        return None

    indices = np.asarray(sorted(crossings), dtype=int)
    psi_rad = np.deg2rad(psi_deg[indices])
    radii = np.asarray([crossings[int(index)] for index in indices])
    xs, ys = radii * np.cos(psi_rad), radii * np.sin(psi_rad)
    horizontal, vertical = _axis_extents(psi_deg, crossings, xs, ys)
    if horizontal is None or vertical is None:
        return None
    a = 0.5 * (horizontal[0] + horizontal[1])
    b = 0.5 * (vertical[0] + vertical[1])
    if not (math.isfinite(a) and math.isfinite(b)) or a <= 1.0e-6 or b <= 1.0e-6:
        return None
    exponent, rms = _fit_exponent(psi_rad, radii, a, b)
    h_bw = math.degrees(math.atan(horizontal[0]) + math.atan(horizontal[1]))
    v_bw = math.degrees(math.atan(vertical[0]) + math.atan(vertical[1]))
    return {
        "shape_exponent": float(exponent),
        "fit_residual_percent": float(100.0 * rms / max((a + b) / 2.0, 1.0e-6)),
        "horizontal_beamwidth_deg": float(h_bw),
        "vertical_beamwidth_deg": float(v_bw),
        "aspect_ratio": float(h_bw / max(v_bw, 1.0e-6)),
    }


def _spherical_di_db(
    theta_grid_deg: np.ndarray,
    phi_grid_deg: np.ndarray,
    spl_grid_db: np.ndarray,
    *,
    hemisphere: bool = False,
) -> float | None:
    values = calculate_di_from_spherical_grid(
        theta_grid_deg,
        phi_grid_deg,
        spl_grid_db[None, :, :],
        hemisphere=hemisphere,
    )
    return values[0] if values else None


def beam_shape_summary(
    theta_deg: Any,
    phi_deg: Any,
    spl_norm_db: Any,
    frequencies_hz: Any,
    *,
    level_db: float = BEAM_LEVEL_DB,
    hemisphere: bool = False,
) -> dict[str, Any] | None:
    """Return JSON-ready per-frequency beam-shape fields (v1 lines 236-312)."""

    theta, phi = _as_float_array(theta_deg), _as_float_array(phi_deg)
    spl, frequencies = _as_float_array(spl_norm_db), _as_float_array(frequencies_hz)
    if (
        theta.ndim != 1
        or phi.ndim != 1
        or theta.size < 2
        or phi.size < 3
        or spl.ndim != 3
        or spl.shape != (frequencies.size, theta.size, phi.size)
    ):
        return None

    fields: dict[str, list[float | None]] = {
        "shape_exponent": [],
        "fit_residual_percent": [],
        "horizontal_beamwidth_deg": [],
        "vertical_beamwidth_deg": [],
        "aspect_ratio": [],
        "spherical_di_db": [],
    }
    valid: list[bool] = []
    for index in range(frequencies.size):
        grid = spl[index]
        wrapped_phi, wrapped_grid = _wrap_phi_columns(phi, grid)
        # Each ray rejects only the invalid interpolated cells it crosses; a
        # single bad spherical cell must not disable all 144 beam-fit rays.
        fit = _fit_frequency(theta, wrapped_phi, wrapped_grid, level_db=level_db)
        if fit is None:
            for key in list(fields)[:5]:
                fields[key].append(None)
            valid.append(False)
        else:
            fields["shape_exponent"].append(round(fit["shape_exponent"], 3))
            fields["fit_residual_percent"].append(round(fit["fit_residual_percent"], 2))
            fields["horizontal_beamwidth_deg"].append(round(fit["horizontal_beamwidth_deg"], 2))
            fields["vertical_beamwidth_deg"].append(round(fit["vertical_beamwidth_deg"], 2))
            fields["aspect_ratio"].append(round(fit["aspect_ratio"], 3))
            valid.append(True)
        di = _spherical_di_db(theta, phi, grid, hemisphere=hemisphere)
        fields["spherical_di_db"].append(None if di is None else round(di, 2))
    return {
        "frequencies": [float(value) for value in frequencies],
        **fields,
        "level_db": float(level_db),
        "di_domain": "full_sphere",
        "di_sampling_domain": "front_hemisphere_rear_zero" if hemisphere else "full_sphere",
        "valid": valid,
    }


__all__ = ["BEAM_LEVEL_DB", "beam_shape_summary"]
