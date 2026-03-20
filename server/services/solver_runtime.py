"""Service-layer adapter for solver/OCC runtime integration."""

from __future__ import annotations

from typing import Any, Dict, Optional

from solver.contract import normalize_mesh_validation_mode
from solver_bootstrap import (
    BEMPP_RUNTIME_READY,
    GMSH_OCC_RUNTIME_READY,
    SOLVER_AVAILABLE,
    WAVEGUIDE_BUILDER_AVAILABLE,
    get_dependency_status,
)

try:
    from solver import BEMSolver
except ImportError:
    BEMSolver = None  # type: ignore[assignment,misc]

try:
    from solver.waveguide_builder import build_waveguide_mesh
except ImportError:
    build_waveguide_mesh = None  # type: ignore[assignment]


def get_settings_capabilities() -> Dict[str, Any]:
    return {
        "simulationBasic": {
            "available": True,
            "controls": [
                "device_mode",
                "mesh_validation_mode",
                "frequency_spacing",
                "verbose",
            ],
            "notes": "Current backend support is limited to the existing /api/solve runtime overrides.",
        },
        "simulationAdvanced": {
            "available": True,
            "controls": [
                "use_burton_miller",
            ],
            "reason": (
                "The public solve contract exposes Burton-Miller coupling "
                "as the stable advanced override."
            ),
        },
    }


def selected_device_metadata(mode: str = "auto") -> Optional[Dict[str, Any]]:
    if not SOLVER_AVAILABLE:
        return None

    try:
        from solver.device_interface import selected_device_metadata as _selected_device_metadata
    except ImportError:
        return None

    try:
        return _selected_device_metadata(mode)
    except Exception:
        return None


def render_all_charts(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from solver.charts import render_all_charts as _render_all_charts
    except ImportError as exc:
        raise RuntimeError(f"Chart renderer not available: {exc}") from exc

    return _render_all_charts(payload)


def render_directivity_plot(
    frequencies: Any,
    directivity: Any,
    *,
    reference_level: Optional[float] = None,
) -> Optional[str]:
    try:
        from solver.directivity_plot import render_directivity_plot as _render_directivity_plot
    except ImportError as exc:
        raise RuntimeError(f"Matplotlib not available: {exc}") from exc

    return _render_directivity_plot(
        frequencies,
        directivity,
        reference_level=reference_level,
    )
