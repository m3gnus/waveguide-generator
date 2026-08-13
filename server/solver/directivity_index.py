"""Standards-aligned loudspeaker directivity-index integration.

Directivity index is a single spherical quantity: ten times the base-ten
logarithm of the reference-axis mean-square pressure divided by its average
over all directions.  A horizontal or vertical cut can have a useful *plane*
index, but those partial indices are not the conventional loudspeaker DI.

This module integrates a complete spherical result grid directly. Horizontal,
vertical, and diagonal curves are display cuts through that field; no subset
or selection of those curves can change the reported DI. Every average is
performed in linear power and weighted by the solid angle represented by each
sample.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


_DB_TO_NEPER = math.log(10.0) / 10.0
_NEPER_TO_DB = 10.0 / math.log(10.0)
_ANGLE_TOLERANCE_DEG = 1.0e-6


def _log_weighted_mean_energy(levels_db: np.ndarray, weights: np.ndarray) -> float | None:
    """Return ln(weighted mean-square pressure) without overflowing."""

    levels = np.asarray(levels_db, dtype=np.float64).reshape(-1)
    area_weights = np.asarray(weights, dtype=np.float64).reshape(-1)
    if (
        levels.size == 0
        or levels.shape != area_weights.shape
        or not np.all(np.isfinite(levels))
        or not np.all(np.isfinite(area_weights))
        or np.any(area_weights < 0.0)
    ):
        return None
    total_weight = float(np.sum(area_weights))
    if total_weight <= 0.0 or not math.isfinite(total_weight):
        return None
    shift_db = float(np.max(levels))
    scaled_mean = float(
        np.sum(area_weights * np.power(10.0, (levels - shift_db) / 10.0)) / total_weight
    )
    if scaled_mean <= 0.0 or not math.isfinite(scaled_mean):
        return None
    return shift_db * _DB_TO_NEPER + math.log(scaled_mean)


def _di_from_weighted_levels(
    levels_db: np.ndarray,
    weights: np.ndarray,
    reference_levels_db: np.ndarray,
    *,
    represented_sphere_fraction: float = 1.0,
) -> float | None:
    """Calculate DI while treating any unrepresented rear domain as silent."""

    log_average_over_sampled_domain = _log_weighted_mean_energy(levels_db, weights)
    reference = np.asarray(reference_levels_db, dtype=np.float64).reshape(-1)
    log_reference = _log_weighted_mean_energy(reference, np.ones(reference.size))
    if log_average_over_sampled_domain is None or log_reference is None:
        return None
    if not 0.0 < represented_sphere_fraction <= 1.0:
        return None
    # ``weights`` are normalized over the sampled domain.  A hemisphere from
    # an infinite-baffle solve represents half of the full sphere; the absent
    # rear hemisphere has zero radiated power.
    log_full_sphere_average = log_average_over_sampled_domain + math.log(
        represented_sphere_fraction
    )
    di = _NEPER_TO_DB * (log_reference - log_full_sphere_average)
    return di if math.isfinite(di) else None


def _cell_edges(nodes: np.ndarray, lower: float, upper: float) -> np.ndarray | None:
    values = np.asarray(nodes, dtype=np.float64)
    if (
        values.ndim != 1
        or values.size < 2
        or not np.all(np.isfinite(values))
        or not np.all(np.diff(values) > 0.0)
        or values[0] < lower - _ANGLE_TOLERANCE_DEG
        or values[-1] > upper + _ANGLE_TOLERANCE_DEG
    ):
        return None
    edges = np.empty(values.size + 1, dtype=np.float64)
    edges[0], edges[-1] = lower, upper
    edges[1:-1] = 0.5 * (values[:-1] + values[1:])
    if not np.all(np.diff(edges) > 0.0):
        return None
    return edges


def _theta_weights(theta_deg: np.ndarray, maximum_deg: float) -> np.ndarray | None:
    if (
        abs(float(theta_deg[0])) > _ANGLE_TOLERANCE_DEG
        or abs(float(theta_deg[-1]) - maximum_deg) > _ANGLE_TOLERANCE_DEG
    ):
        return None
    edges_deg = _cell_edges(theta_deg, 0.0, maximum_deg)
    if edges_deg is None:
        return None
    edges = np.deg2rad(edges_deg)
    # Fraction of the sampled domain's solid angle represented by each band.
    weights = np.cos(edges[:-1]) - np.cos(edges[1:])
    total = float(np.sum(weights))
    if total <= 0.0 or not math.isfinite(total):
        return None
    return weights / total


def _periodic_phi_weights(phi_deg: np.ndarray) -> np.ndarray | None:
    phi = np.asarray(phi_deg, dtype=np.float64)
    if (
        phi.ndim != 1
        or phi.size < 3
        or not np.all(np.isfinite(phi))
        or not np.all(np.diff(phi) > 0.0)
    ):
        return None
    previous = np.concatenate([[phi[-1] - 360.0], phi[:-1]])
    following = np.concatenate([phi[1:], [phi[0] + 360.0]])
    widths = 0.5 * (following - previous)
    if np.any(widths <= 0.0) or not np.all(np.isfinite(widths)):
        return None
    return widths / float(np.sum(widths))


def calculate_di_from_spherical_grid(
    theta_deg: Any,
    phi_deg: Any,
    levels_db: Any,
    *,
    hemisphere: bool = False,
) -> list[float | None]:
    """Integrate one DI value per frequency from a spherical pressure grid.

    ``levels_db`` may be absolute or normalized pressure level because the
    reference-axis level is taken from the theta=0 row.  A hemisphere is valid
    for the app's infinite-baffle solve, where radiation into the missing rear
    hemisphere is zero; it therefore has a 3.0103 dB floor for uniform forward
    radiation.
    """

    try:
        theta = np.asarray(theta_deg, dtype=np.float64)
        phi = np.asarray(phi_deg, dtype=np.float64)
        grids = np.asarray(levels_db, dtype=np.float64)
    except (TypeError, ValueError):
        return []
    if (
        theta.ndim != 1
        or phi.ndim != 1
        or grids.ndim != 3
        or grids.shape[1:] != (theta.size, phi.size)
    ):
        return []
    theta_maximum = 90.0 if hemisphere else 180.0
    theta_weights = _theta_weights(theta, theta_maximum)
    phi_weights = _periodic_phi_weights(phi)
    if theta_weights is None or phi_weights is None:
        return [None] * grids.shape[0]
    direction_weights = theta_weights[:, None] * phi_weights[None, :]
    represented_fraction = 0.5 if hemisphere else 1.0
    values: list[float | None] = []
    for grid in grids:
        values.append(
            _di_from_weighted_levels(
                grid,
                direction_weights,
                grid[0],
                represented_sphere_fraction=represented_fraction,
            )
        )
    return values


__all__ = ["calculate_di_from_spherical_grid"]
