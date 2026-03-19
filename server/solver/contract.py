import math
from dataclasses import dataclass
from typing import Any, Dict

VALID_MESH_VALIDATION_MODES = {"strict", "warn", "off"}


@dataclass(frozen=True)
class SolverRunOptions:
    use_optimized: bool = True
    verbose: bool = False
    mesh_validation_mode: str = "warn"


def normalize_mesh_validation_mode(value: Any) -> str:
    mode = str(value or "warn").strip().lower()
    if mode not in VALID_MESH_VALIDATION_MODES:
        raise ValueError(
            f"mesh_validation_mode must be one of: {', '.join(sorted(VALID_MESH_VALIDATION_MODES))}."
        )
    return mode


def frequency_failure(
    frequency_hz: float,
    stage: str,
    code: str,
    detail: str,
) -> Dict[str, Any]:
    return {
        "frequency_hz": float(frequency_hz),
        "stage": str(stage),
        "code": str(code),
        "detail": str(detail),
    }


def _coerce_finite_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed):
        return float(default)
    return parsed


def build_directivity_metadata(
    polar_config: Any,
    observation_info: Any,
) -> Dict[str, Any]:
    config = polar_config if isinstance(polar_config, dict) else {}
    observation = observation_info if isinstance(observation_info, dict) else {}

    raw_angle_range = config.get("angle_range", [0.0, 180.0, 37])
    if isinstance(raw_angle_range, (list, tuple)) and len(raw_angle_range) == 3:
        angle_start = _coerce_finite_float(raw_angle_range[0], 0.0)
        angle_end = _coerce_finite_float(raw_angle_range[1], 180.0)
        try:
            sample_count = max(1, int(raw_angle_range[2]))
        except (TypeError, ValueError):
            sample_count = 37
    else:
        angle_start = 0.0
        angle_end = 180.0
        sample_count = 37

    angular_step = None
    if sample_count > 1:
        angular_step = (angle_end - angle_start) / float(sample_count - 1)

    raw_axes = config.get("enabled_axes", ["horizontal", "vertical", "diagonal"])
    if not isinstance(raw_axes, (list, tuple)):
        raw_axes = ["horizontal", "vertical", "diagonal"]

    enabled_axes = []
    for axis in raw_axes:
        value = str(axis or "").strip().lower()
        if value in {"horizontal", "vertical", "diagonal"} and value not in enabled_axes:
            enabled_axes.append(value)
    if not enabled_axes:
        enabled_axes = ["horizontal", "vertical", "diagonal"]

    observation_origin = str(config.get("observation_origin", "mouth")).strip().lower()
    if observation_origin not in {"mouth", "throat"}:
        observation_origin = "mouth"

    return {
        "angle_range_degrees": [angle_start, angle_end],
        "sample_count": sample_count,
        "angular_step_degrees": angular_step,
        "enabled_axes": enabled_axes,
        "normalization_angle_degrees": _coerce_finite_float(config.get("norm_angle", 5.0), 5.0),
        "diagonal_angle_degrees": _coerce_finite_float(config.get("inclination", 35.0), 35.0),
        "observation_origin": observation_origin,
        "requested_distance_m": observation.get("requested_distance_m"),
        "effective_distance_m": observation.get("effective_distance_m"),
    }
