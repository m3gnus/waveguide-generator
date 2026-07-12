"""Service-layer adapter for mesher and solver runtime integration."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from solver.contract import normalize_mesh_validation_mode
from solver.bempp_solver import (
    bempp_backend_status,
    opencl_runtime_status,
    solve_bempp_from_msh,
)
from solver.metal_solver import (
    is_metal_fast_solve_ready,
    metal_backend_status,
    metal_fast_solve_unavailable_reason,
    normalize_solver_backend,
    resolve_solver_backend,
    solve_circsym_from_params,
    solve_metal_from_msh,
)
from solver_bootstrap import (
    BEMPP_SOLVER_AVAILABLE,
    BEMPP_SOLVER_READY,
    HORNLAB_MESHER_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    METAL_SOLVER_AVAILABLE,
    SOLVER_AVAILABLE,
    get_dependency_status,
)
from solver.theme_preview import (
    DEFAULT_CHART_THEME,
    build_theme_montage_b64,
    list_available_themes,
    resolve_chart_theme,
)

logger = logging.getLogger(__name__)

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
    metal_status = metal_backend_status()
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
                    "available": is_metal_fast_solve_ready(metal_status),
                    "label": "Metal BEM",
                    "status": metal_status,
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
    """Render the four result charts, theme-aware, via ``hornlab_plots``.

    The canonical ``hornlab_plots`` renderer is preferred; ``payload['theme']``
    (falling back to :data:`DEFAULT_CHART_THEME`) selects the theme. When the
    sibling package is not installed the in-repo legacy ``solver.charts``
    renderer is used instead — it ignores the theme and reproduces the former
    hardcoded-dark look. The payload carries ``reference`` unchanged; older
    ``hornlab_plots`` versions and the legacy renderer safely ignore that
    unknown key, while reference overlays require a supporting version.
    """
    theme = resolve_chart_theme(payload.get("theme"))
    try:
        import hornlab_plots
    except ImportError:
        try:
            from solver.charts import render_all_charts as _legacy_render_all_charts
        except ImportError as exc:
            raise RuntimeError(f"Chart renderer not available: {exc}") from exc
        return _legacy_render_all_charts(payload)

    return hornlab_plots.render_all_charts_b64(payload, theme=theme)


def render_directivity_plot(
    frequencies: Any,
    directivity: Any,
    *,
    reference_level: Optional[float] = None,
    theme: Optional[str] = None,
    reference_frequencies: Optional[List[float]] = None,
    reference_directivity: Optional[Dict[str, Any]] = None,
    reference_label: Optional[str] = None,
) -> Optional[str]:
    """Render the directivity heatmap, theme-aware, via ``hornlab_plots``.

    Falls back to the in-repo legacy ``solver.directivity_plot`` renderer (which
    ignores the theme) when the sibling package is not installed.
    """
    ref = -6.0 if reference_level is None else reference_level
    resolved_theme = resolve_chart_theme(theme)
    try:
        import hornlab_plots
    except ImportError:
        try:
            from solver.directivity_plot import (
                render_directivity_plot as _legacy_render_directivity_plot,
            )
        except ImportError as exc:
            raise RuntimeError(f"Matplotlib not available: {exc}") from exc
        return _legacy_render_directivity_plot(
            frequencies,
            directivity,
            reference_level=ref,
        )

    reference_kwargs: Dict[str, Any] = {}
    if reference_directivity is not None:
        reference_kwargs = {
            "reference_frequencies": reference_frequencies,
            "reference_directivity": reference_directivity,
            "reference_label": reference_label,
        }

    try:
        return hornlab_plots.directivity_heatmap_from_legacy_dict(
            frequencies,
            directivity,
            reference_level=ref,
            theme=resolved_theme,
            **reference_kwargs,
        )
    except TypeError as exc:
        if not reference_kwargs:
            raise
        logger.warning(
            "hornlab_plots rejected directivity reference arguments; "
            "retrying without reference overlays: %s",
            exc,
        )
        return hornlab_plots.directivity_heatmap_from_legacy_dict(
            frequencies,
            directivity,
            reference_level=ref,
            theme=resolved_theme,
        )
