import math
from dataclasses import dataclass
from typing import Any, Dict, List

VALID_MESH_VALIDATION_MODES = {"strict", "warn", "off"}
_DEFAULT_DIRECTIVITY_PLANES = (
    ("horizontal", 0.0),
    ("vertical", 90.0),
    ("diagonal", 35.0),
)


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


def normalize_directivity_planes(polar_config: Any) -> List[Dict[str, float | str]]:
    config = polar_config if isinstance(polar_config, dict) else {}
    raw_axes = config.get("enabled_axes")
    default_axes = [plane_id for plane_id, _phi in _DEFAULT_DIRECTIVITY_PLANES]
    if not isinstance(raw_axes, (list, tuple)):
        raw_axes = default_axes

    enabled_axes = []
    seen = set()
    for axis in raw_axes:
        value = str(axis or "").strip().lower()
        if value not in default_axes or value in seen:
            continue
        seen.add(value)
        enabled_axes.append(value)
    if not enabled_axes:
        enabled_axes = default_axes

    diagonal_angle = _coerce_finite_float(config.get("inclination", 35.0), 35.0)
    phi_by_axis = {
        "horizontal": 0.0,
        "vertical": 90.0,
        "diagonal": diagonal_angle,
    }
    return [
        {"id": axis, "phi_degrees": phi_by_axis[axis]}
        for axis in enabled_axes
    ]


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

    planes = normalize_directivity_planes(config)
    enabled_axes = [str(plane["id"]) for plane in planes]

    observation_origin = str(config.get("observation_origin", "mouth")).strip().lower()
    if observation_origin not in {"mouth", "throat"}:
        observation_origin = "mouth"

    return {
        "angle_range_degrees": [angle_start, angle_end],
        "sample_count": sample_count,
        "angular_step_degrees": angular_step,
        "enabled_axes": enabled_axes,
        "planes": planes,
        "normalization_angle_degrees": _coerce_finite_float(config.get("norm_angle", 5.0), 5.0),
        "diagonal_angle_degrees": _coerce_finite_float(config.get("inclination", 35.0), 35.0),
        "observation_origin": observation_origin,
        "requested_distance_m": observation.get("requested_distance_m"),
        "effective_distance_m": observation.get("effective_distance_m"),
    }
