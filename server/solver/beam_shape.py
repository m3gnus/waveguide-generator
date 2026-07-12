"""Forward beam shape ("wavefront bubble") analysis of balloon SPL grids.

Reduces a spherical balloon dataset to per-frequency scalars: the superellipse
exponent of the forward -6 dB beam contour, its fit residual, horizontal and
vertical beamwidths, and a full-grid spherical directivity index.

The method follows the publicly documented Boundary Lab "Forward Beam Shape"
analysis (docs/advanced/forward-beam-shape.md in the boundary-lab repo):
project the forward hemisphere onto the plane tangent to the axis (a circular
cone stays circular there, so wide beams are not distorted square), march
radial rays to the first -6 dB crossing, and fit an aspect-corrected
superellipse exponent to the crossing loop. This is an independent
implementation written from that published description.

For center rays in the tangent plane the geometry collapses to meridians:
a tangent-plane ray at azimuth psi samples the sphere along constant phi=psi
with r = tan(theta), which is what the sampler below exploits.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

BEAM_LEVEL_DB = -6.0
MAX_FRONT_ANGLE_DEG = 89.0
# Multiple of 4 so the horizontal/vertical axis rays exist exactly.
RAY_COUNT = 144
RAY_SAMPLES = 181
EXPONENT_BOUNDS = (0.75, 8.0)
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0


def _as_float_array(values: Any) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


def _wrap_phi_columns(phi_deg: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Append the phi=first column at first+360 so bilinear lookups wrap."""
    wrapped_phi = np.concatenate([phi_deg, [phi_deg[0] + 360.0]])
    wrapped_values = np.concatenate([values, values[:, :1]], axis=1)
    return wrapped_phi, wrapped_values


def _interp_meridian(
    theta_grid_deg: np.ndarray,
    phi_grid_deg: np.ndarray,
    spl_grid_db: np.ndarray,
    phi_query_deg: float,
    theta_query_deg: np.ndarray,
) -> np.ndarray:
    """Bilinear sample of the (theta, phi) grid along one constant-phi ray."""
    phi = float(phi_query_deg) % 360.0
    j = int(np.searchsorted(phi_grid_deg, phi, side="right") - 1)
    j = max(0, min(j, phi_grid_deg.size - 2))
    span = phi_grid_deg[j + 1] - phi_grid_deg[j]
    w = 0.0 if span <= 0 else (phi - phi_grid_deg[j]) / span
    column = (1.0 - w) * spl_grid_db[:, j] + w * spl_grid_db[:, j + 1]
    return np.interp(theta_query_deg, theta_grid_deg, column)


def _ray_crossing_radius(
    theta_samples_deg: np.ndarray,
    spl_along_ray_db: np.ndarray,
    level_db: float,
) -> float | None:
    """First outward crossing of ``level_db``, as a tangent-plane radius."""
    finite = np.isfinite(spl_along_ray_db)
    if not np.all(finite):
        return None
    below = spl_along_ray_db <= level_db
    if not np.any(below):
        return None
    k = int(np.argmax(below))
    if k == 0:
        return None
    r = np.tan(np.deg2rad(theta_samples_deg))
    s0 = float(spl_along_ray_db[k - 1])
    s1 = float(spl_along_ray_db[k])
    if s0 == s1:
        return float(r[k])
    fraction = (s0 - level_db) / (s0 - s1)
    return float(r[k - 1] + (r[k] - r[k - 1]) * fraction)


def _superellipse_radius(
    psi_rad: np.ndarray, a: float, b: float, exponent: float
) -> np.ndarray:
    term = (
        np.abs(np.cos(psi_rad) / a) ** exponent
        + np.abs(np.sin(psi_rad) / b) ** exponent
    )
    return term ** (-1.0 / exponent)


def _fit_exponent(
    psi_rad: np.ndarray, radii: np.ndarray, a: float, b: float
) -> tuple[float, float]:
    """Golden-section minimize of mean squared radial error over the exponent."""

    def objective(exponent: float) -> float:
        model = _superellipse_radius(psi_rad, a, b, exponent)
        return float(np.mean((radii - model) ** 2))

    lo, hi = EXPONENT_BOUNDS
    x1 = hi - _GOLDEN * (hi - lo)
    x2 = lo + _GOLDEN * (hi - lo)
    f1, f2 = objective(x1), objective(x2)
    for _ in range(48):
        if f1 <= f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - _GOLDEN * (hi - lo)
            f1 = objective(x1)
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + _GOLDEN * (hi - lo)
            f2 = objective(x2)
    exponent = (lo + hi) / 2.0
    return exponent, math.sqrt(objective(exponent))


def _signed_extent(values: np.ndarray) -> tuple[float, float] | None:
    positive = values[values > 0.0]
    negative = values[values < 0.0]
    if positive.size == 0 or negative.size == 0:
        return None
    return float(np.max(positive)), float(-np.min(negative))


def _axis_extents(
    psi_deg: np.ndarray,
    crossings: dict[int, float],
    xs: np.ndarray,
    ys: np.ndarray,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """(positive, negative) tangent extents along the horizontal/vertical axes.

    Prefers the exact axis rays (psi 0/180 and 90/270); falls back to signed
    extents of the whole crossing loop when an axis ray had no crossing.
    """

    def ray_radius(angle: float) -> float | None:
        matches = np.where(np.isclose(psi_deg, angle))[0]
        if matches.size == 0:
            return None
        return crossings.get(int(matches[0]))

    h_pos, h_neg = ray_radius(0.0), ray_radius(180.0)
    v_pos, v_neg = ray_radius(90.0), ray_radius(270.0)
    horizontal = (h_pos, h_neg) if h_pos is not None and h_neg is not None else None
    vertical = (v_pos, v_neg) if v_pos is not None and v_neg is not None else None
    if horizontal is None:
        horizontal = _signed_extent(xs)
    if vertical is None:
        vertical = _signed_extent(ys)
    return horizontal, vertical


def _fit_frequency(
    theta_grid_deg: np.ndarray,
    phi_grid_deg: np.ndarray,
    spl_grid_db: np.ndarray,
    *,
    level_db: float,
) -> dict[str, float] | None:
    theta_max = min(MAX_FRONT_ANGLE_DEG, float(theta_grid_deg[-1]))
    theta_samples = np.linspace(0.0, theta_max, RAY_SAMPLES)
    psi_deg = np.arange(RAY_COUNT, dtype=np.float64) * (360.0 / RAY_COUNT)

    crossings: dict[int, float] = {}
    for index, psi in enumerate(psi_deg):
        spl_ray = _interp_meridian(
            theta_grid_deg, phi_grid_deg, spl_grid_db, psi, theta_samples
        )
        radius = _ray_crossing_radius(theta_samples, spl_ray, level_db)
        if radius is not None:
            crossings[index] = radius

    if len(crossings) < max(24, RAY_COUNT // 4):
        return None

    indices = np.array(sorted(crossings), dtype=int)
    psi_rad = np.deg2rad(psi_deg[indices])
    radii = np.array([crossings[int(i)] for i in indices])
    xs = radii * np.cos(psi_rad)
    ys = radii * np.sin(psi_rad)

    horizontal, vertical = _axis_extents(psi_deg, crossings, xs, ys)
    if horizontal is None or vertical is None:
        return None
    a = 0.5 * (horizontal[0] + horizontal[1])
    b = 0.5 * (vertical[0] + vertical[1])
    if not (math.isfinite(a) and math.isfinite(b)) or a <= 1e-6 or b <= 1e-6:
        return None

    exponent, rms = _fit_exponent(psi_rad, radii, a, b)
    residual_percent = 100.0 * rms / max((a + b) / 2.0, 1e-6)
    horizontal_bw = math.degrees(math.atan(horizontal[0]) + math.atan(horizontal[1]))
    vertical_bw = math.degrees(math.atan(vertical[0]) + math.atan(vertical[1]))
    return {
        "shape_exponent": float(exponent),
        "fit_residual_percent": float(residual_percent),
        "horizontal_beamwidth_deg": float(horizontal_bw),
        "vertical_beamwidth_deg": float(vertical_bw),
        "aspect_ratio": float(horizontal_bw / max(vertical_bw, 1e-6)),
    }


def _spherical_di_db(
    theta_grid_deg: np.ndarray, spl_grid_db: np.ndarray
) -> float | None:
    """Solid-angle-weighted directivity index over the sampled grid.

    The regular theta/phi grid over-samples the poles, so rows are weighted
    by sin(theta). SPL is already normalized to the reference axis; the DI is
    relative to an isotropic radiator over the sampled domain (full sphere,
    or hemisphere for half-space balloons).
    """
    weights = np.sin(np.deg2rad(theta_grid_deg))
    weights = np.where(np.isfinite(weights) & (weights > 0.0), weights, 0.0)
    weight_total = float(np.sum(weights) * spl_grid_db.shape[1])
    if weight_total <= 0.0:
        return None
    finite = np.isfinite(spl_grid_db)
    if not np.all(finite):
        return None
    energy = np.power(10.0, spl_grid_db / 10.0)
    mean_energy = float(np.sum(energy * weights[:, None]) / weight_total)
    if mean_energy <= 0.0:
        return None
    return float(-10.0 * math.log10(mean_energy))


def beam_shape_summary(
    theta_deg: Any,
    phi_deg: Any,
    spl_norm_db: Any,
    frequencies_hz: Any,
    *,
    level_db: float = BEAM_LEVEL_DB,
    hemisphere: bool = False,
) -> dict[str, Any] | None:
    """Per-frequency forward beam shape + spherical DI from a balloon grid.

    Args:
        theta_deg: (T,) ascending polar angles, 0 = on-axis.
        phi_deg: (P,) ascending azimuths in [0, 360), no wrap duplicate.
        spl_norm_db: (F, T, P) normalized SPL in dB (0 dB on-axis).
        frequencies_hz: (F,) solve frequencies.

    Returns a JSON-ready dict of per-frequency lists (None marks frequencies
    where the -6 dB contour did not exist or the fit failed), or None when
    the inputs are unusable.
    """
    theta = _as_float_array(theta_deg)
    phi = _as_float_array(phi_deg)
    spl = _as_float_array(spl_norm_db)
    freqs = _as_float_array(frequencies_hz)
    if (
        theta.ndim != 1
        or phi.ndim != 1
        or theta.size < 2
        or phi.size < 3
        or spl.ndim != 3
        or spl.shape != (freqs.size, theta.size, phi.size)
    ):
        return None

    shape_exponent: list[float | None] = []
    fit_residual: list[float | None] = []
    horizontal_bw: list[float | None] = []
    vertical_bw: list[float | None] = []
    aspect: list[float | None] = []
    di_db: list[float | None] = []

    for freq_index in range(freqs.size):
        grid = spl[freq_index]
        phi_wrapped, grid_wrapped = _wrap_phi_columns(phi, grid)
        fit = (
            _fit_frequency(theta, phi_wrapped, grid_wrapped, level_db=level_db)
            if np.all(np.isfinite(grid))
            else None
        )
        if fit is None:
            shape_exponent.append(None)
            fit_residual.append(None)
            horizontal_bw.append(None)
            vertical_bw.append(None)
            aspect.append(None)
        else:
            shape_exponent.append(round(fit["shape_exponent"], 3))
            fit_residual.append(round(fit["fit_residual_percent"], 2))
            horizontal_bw.append(round(fit["horizontal_beamwidth_deg"], 2))
            vertical_bw.append(round(fit["vertical_beamwidth_deg"], 2))
            aspect.append(round(fit["aspect_ratio"], 3))
        di = _spherical_di_db(theta, grid)
        di_db.append(None if di is None else round(di, 2))

    return {
        "frequencies": [float(value) for value in freqs],
        "shape_exponent": shape_exponent,
        "fit_residual_percent": fit_residual,
        "horizontal_beamwidth_deg": horizontal_bw,
        "vertical_beamwidth_deg": vertical_bw,
        "aspect_ratio": aspect,
        "spherical_di_db": di_db,
        "level_db": float(level_db),
        "di_domain": "hemisphere" if hemisphere else "sphere",
        "valid": [value is not None for value in shape_exponent],
    }
