from dataclasses import dataclass
from typing import Any, Dict

VALID_MESH_VALIDATION_MODES = {"strict", "warn", "off"}


@dataclass(frozen=True)
class SolverRunOptions:
    use_optimized: bool = True
    enable_symmetry: bool = True
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
