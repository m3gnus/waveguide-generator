"""Plane directivity-index integration ported from v1.

The axisymmetric factor-of-two integral and missing-sample rules are from v1
``server/solver/directivity_index.py:11-75``.
"""

from __future__ import annotations

import math

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
            usable = [
                point
                for point in (pattern or [])
                if point and len(point) >= 2 and point[1] is not None
            ]
            if len(usable) < 3:
                values.append(None)
                continue
            samples: list[tuple[float, float]] = []
            for point in usable:
                try:
                    angle, level = float(point[0]), float(point[1])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(angle) and math.isfinite(level):
                    samples.append((angle, level))
            if len(samples) < 3:
                values.append(None)
                continue
            samples.sort(key=lambda item: item[0])
            angles = np.deg2rad([sample[0] for sample in samples])
            levels_db = np.asarray([sample[1] for sample in samples], dtype=float)
            shift_db = float(np.max(levels_db))
            scaled_energy = np.power(10.0, (levels_db - shift_db) / 10.0)
            scaled_integral = float(_trapezoid(scaled_energy * np.sin(angles), angles))
            if scaled_integral <= 0.0 or not math.isfinite(scaled_integral):
                values.append(None)
                continue
            log_integral = shift_db * math.log(10.0) / 10.0 + math.log(scaled_integral)
            log_ratio = math.log(2.0) - log_integral
            if log_ratio <= 0.0:
                values.append(0.0)
                continue
            di = 10.0 / math.log(10.0) * log_ratio
            values.append(di if math.isfinite(di) else None)
        per_plane[str(plane_id)] = values
    return per_plane


__all__ = ["calculate_di_from_polar_patterns"]
