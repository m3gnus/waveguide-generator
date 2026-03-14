"""
Miscellaneous routes: health, updates, chart rendering, directivity rendering, file export.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from contracts import ChartsRenderRequest, DirectivityRenderRequest
from services.solver_runtime import (
    SOLVER_AVAILABLE,
    BEMPP_RUNTIME_READY,
    WAVEGUIDE_BUILDER_AVAILABLE,
    GMSH_OCC_RUNTIME_READY,
    get_dependency_status,
    get_settings_capabilities,
    render_all_charts,
    render_directivity_plot,
    selected_device_metadata,
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
    device_info = selected_device_metadata("auto")

    return {
        "status": "ok",
        "solver": "bempp-cl" if SOLVER_AVAILABLE else "unavailable",
        "solverReady": BEMPP_RUNTIME_READY,
        "occBuilderReady": WAVEGUIDE_BUILDER_AVAILABLE and GMSH_OCC_RUNTIME_READY,
        "dependencies": dependency_status,
        "capabilities": get_settings_capabilities(),
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
        charts = render_all_charts(request.model_dump())
        result = {}
        for key, b64 in charts.items():
            if b64 is not None:
                result[key] = f"data:image/png;base64,{b64}"
        return {"charts": result}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        image_b64 = render_directivity_plot(
            request.frequencies,
            request.directivity,
            reference_level=request.reference_level,
        )
        if image_b64 is None:
            raise HTTPException(status_code=400, detail="No directivity patterns to render")
        return {"image": f"data:image/png;base64,{image_b64}"}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Rendering failed: {exc}") from exc


@router.post("/api/export-file")
async def export_file(
    file: UploadFile = File(...),
    folder_path: str = Form(...),
) -> Dict[str, str]:
    """
    Save an exported file to a server-side folder.
    folder_path should be a relative path from repo root (e.g., 'output/my_project').
    """
    if not folder_path:
        raise HTTPException(status_code=400, detail="folder_path is required")

    # Prevent path traversal attacks
    folder_path = folder_path.strip()
    if ".." in folder_path or folder_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid folder path")

    # Get repo root (assuming server is in /server subdirectory)
    repo_root = Path(__file__).parent.parent.parent
    target_dir = repo_root / folder_path

    try:
        # Create folder if it doesn't exist
        target_dir.mkdir(parents=True, exist_ok=True)

        # Save file
        file_path = target_dir / file.filename
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        logger.info(f"File exported: {file_path}")
        return {
            "status": "success",
            "path": str(file_path),
            "filename": file.filename
        }
    except Exception as exc:
        logger.error(f"Export failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc
