"""Service-layer adapter for mesher and solver runtime integration."""

from __future__ import annotations

from typing import Any, Dict, Optional

from solver.contract import normalize_mesh_validation_mode
from solver.bempp_solver import (
    bempp_backend_status,
    opencl_runtime_status,
    solve_bempp_from_msh,
)
from solver.metal_solver import (
    metal_backend_status,
    normalize_solver_backend,
    resolve_solver_backend,
    solve_metal_from_msh,
)
from solver_bootstrap import (
    BEMPP_SOLVER_AVAILABLE,
    BEMPP_SOLVER_READY,
    HORNLAB_MESHER_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    METAL_SOLVER_AVAILABLE,
    METAL_SOLVER_READY,
    SOLVER_AVAILABLE,
    get_dependency_status,
)

try:
    from solver.mesher_adapter import (
        build_inner_surface_step,
        build_viewport_geometry,
        build_waveguide_mesh,
    )
except ImportError:
    build_inner_surface_step = None  # type: ignore[assignment]
    build_waveguide_mesh = None  # type: ignore[assignment]
    build_viewport_geometry = None  # type: ignore[assignment]

def get_settings_capabilities() -> Dict[str, Any]:
    return {
        "simulationBasic": {
            "available": True,
            "controls": [
                "mesh_validation_mode",
                "frequency_spacing",
                "verbose",
            ],
            "notes": "Current backend support is limited to the existing /api/solve runtime overrides.",
        },
        "simulationAdvanced": {
            "available": True,
            "controls": [
                "solver_backend",
            ],
            "reason": (
                "The public solve contract exposes solver backend selection "
                "as a stable override."
            ),
        },
        "solverBackends": {
            "available": True,
            "default": "auto",
            "backends": {
                "metal": {
                    "available": bool(METAL_SOLVER_READY),
                    "label": "Metal BEM",
                    "status": metal_backend_status(),
                },
                "bempp": {
                    "available": bool(BEMPP_SOLVER_READY),
                    "label": "BEMPP BEM",
                    "status": bempp_backend_status(),
                },
            },
        },
    }


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
