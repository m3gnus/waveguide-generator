"""
Miscellaneous routes: health, updates, chart rendering, directivity rendering.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from models import ChartsRenderRequest, DirectivityRenderRequest
from solver_bootstrap import (
    SOLVER_AVAILABLE,
    BEMPP_RUNTIME_READY,
    WAVEGUIDE_BUILDER_AVAILABLE,
    GMSH_OCC_RUNTIME_READY,
    get_dependency_status,
)
from services.update_service import get_update_status

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def root() -> Dict[str, Any]:
    """Root endpoint."""
    return {
        "name": "MWG Horn BEM Solver",
        "version": "1.0.0",
        "status": "running",
        "solver_available": SOLVER_AVAILABLE,
    }


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint."""
    logger.info("Health check requested")
    dependency_status = get_dependency_status()

    device_info = None
    if SOLVER_AVAILABLE:
        try:
            from solver.device_interface import selected_device_metadata  # noqa: PLC0415
            device_info = selected_device_metadata("auto")
        except Exception:
            pass

    return {
        "status": "ok",
        "solver": "bempp-cl" if SOLVER_AVAILABLE else "unavailable",
        "solverReady": BEMPP_RUNTIME_READY,
        "occBuilderReady": WAVEGUIDE_BUILDER_AVAILABLE and GMSH_OCC_RUNTIME_READY,
        "dependencies": dependency_status,
        "deviceInterface": device_info,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/api/updates/check")
async def check_updates() -> Dict[str, Any]:
    try:
        return get_update_status()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/render-charts")
async def render_charts(request: ChartsRenderRequest) -> Dict[str, Any]:
    """
    Render all result charts as PNG images using Matplotlib.
    Returns base64-encoded PNGs for each chart type.
    """
    try:
        from solver.charts import render_all_charts  # noqa: PLC0415
    except ImportError as exc:
        raise HTTPException(
            status_code=503, detail=f"Chart renderer not available: {exc}"
        ) from exc

    try:
        charts = render_all_charts(request.model_dump())
        result = {}
        for key, b64 in charts.items():
            if b64 is not None:
                result[key] = f"data:image/png;base64,{b64}"
        return {"charts": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chart rendering failed: {exc}") from exc


@router.post("/api/render-directivity")
async def render_directivity(request: DirectivityRenderRequest) -> Dict[str, str]:
    """
    Render directivity heatmap as a PNG image using Matplotlib.
    Returns base64-encoded PNG.
    """
    if not request.frequencies or not request.directivity:
        raise HTTPException(status_code=422, detail="Missing frequencies or directivity data")

    try:
        from solver.directivity_plot import render_directivity_plot  # noqa: PLC0415
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"Matplotlib not available: {exc}") from exc

    try:
        image_b64 = render_directivity_plot(
            request.frequencies,
            request.directivity,
            reference_level=request.reference_level,
        )
        if image_b64 is None:
            raise HTTPException(status_code=400, detail="No directivity patterns to render")
        return {"image": f"data:image/png;base64,{image_b64}"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Rendering failed: {exc}") from exc
