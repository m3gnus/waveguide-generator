"""Plane directivity-index integration ported from v1.

The axisymmetric factor-of-two integral and missing-sample rules are from v1
``server/solver/directivity_index.py:11-75``.
"""

from __future__ import annotations

from typing import Any

import numpy as np


try:
    _trapezoid = np.trapezoid
except AttributeError:  # NumPy <2 compatibility, matching v1 lines 5-8
    _trapezoid = np.trapz


def calculate_di_from_polar_patterns(
    directivity_patterns: dict[str, list[list[list[float | None]]]],
) -> dict[str, list[float | None]]:
    """Integrate each finite polar pattern as ``10 log10(2/integral)``."""

    per_plane: dict[str, list[float | None]] = {}
    for plane_id, patterns in directivity_patterns.items():
        values: list[float | None] = []
        for pattern in patterns:
            usable = [point for point in (pattern or []) if point and len(point) >= 2 and point[1] is not None]
            if len(usable) < 3:
                values.append(None)
                continue
            angles = np.deg2rad([float(point[0]) for point in usable])
            pressure = 10.0 ** (np.asarray([float(point[1]) for point in usable]) / 20.0)
            integral = float(_trapezoid(pressure**2 * np.sin(angles), angles))
            di = max(0.0, float(10.0 * np.log10(2.0 / integral))) if integral > 0.0 else 0.0
            values.append(di)
        per_plane[str(plane_id)] = values
    return per_plane


__all__ = ["calculate_di_from_polar_patterns"]
