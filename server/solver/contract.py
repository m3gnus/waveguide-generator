"""Small result-contract helpers ported from v1 ``solver/contract.py:29-125``."""

from __future__ import annotations

import math
from typing import Any


def frequency_failure(frequency_hz: float, stage: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "frequency_hz": float(frequency_hz),
        "stage": str(stage),
        "code": str(code),
        "detail": str(detail),
    }


def _finite(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def build_directivity_metadata(polar_config: Any, observation: Any) -> dict[str, Any]:
    """Resolve plane, angle, origin, and distance metadata exactly once."""

    config = polar_config if isinstance(polar_config, dict) else {}
    info = observation if isinstance(observation, dict) else {}
    raw_range = config.get("angle_range", [0.0, 180.0, 37])
    if isinstance(raw_range, (list, tuple)) and len(raw_range) == 3:
        start, end = _finite(raw_range[0], 0.0), _finite(raw_range[1], 180.0)
        try:
            count = max(1, int(raw_range[2]))
        except (TypeError, ValueError):
            count = 37
    else:
        start, end, count = 0.0, 180.0, 37
    raw_axes = config.get("enabled_axes", ["horizontal", "vertical"])
    axes = []
    for axis in raw_axes if isinstance(raw_axes, (list, tuple)) else []:
        normalized = str(axis).strip().lower()
        if normalized in {"horizontal", "vertical", "diagonal"} and normalized not in axes:
            axes.append(normalized)
    if not axes:
        axes = ["horizontal", "vertical"]
    inclination = _finite(config.get("inclination", 35.0), 35.0)
    phi = {"horizontal": 0.0, "vertical": 90.0, "diagonal": inclination}
    origin = str(config.get("observation_origin", "mouth")).strip().lower()
    if origin not in {"mouth", "throat"}:
        origin = "mouth"
    return {
        "angle_range_degrees": [start, end],
        "sample_count": count,
        "angular_step_degrees": (end - start) / (count - 1) if count > 1 else None,
        "enabled_axes": axes,
        "planes": [{"id": axis, "phi_degrees": phi[axis]} for axis in axes],
        "normalization_angle_degrees": _finite(config.get("norm_angle", 5.0), 5.0),
        "diagonal_angle_degrees": inclination,
        "observation_origin": origin,
        "requested_distance_m": info.get("requested_distance_m"),
        "effective_distance_m": info.get("effective_distance_m"),
    }


__all__ = ["build_directivity_metadata", "frequency_failure"]
