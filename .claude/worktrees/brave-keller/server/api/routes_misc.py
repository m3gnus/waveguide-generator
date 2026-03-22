"""
Miscellaneous routes: health, updates, chart rendering, directivity rendering, file export,
workspace path/open.
"""

import logging
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from contracts import ChartsRenderRequest, DirectivityRenderRequest
from services.runtime_preflight import collect_runtime_doctor_report
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


def _coerce_form_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


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
    doctor_report = collect_runtime_doctor_report("auto")
    doctor_summary = doctor_report.get("summary") if isinstance(doctor_report.get("summary"), dict) else {}
    solve_ready = doctor_summary.get("solveReady")
    if not isinstance(solve_ready, bool):
        solve_ready = bool(BEMPP_RUNTIME_READY)

    return {
        "status": "ok",
        "solver": "bempp-cl" if SOLVER_AVAILABLE else "unavailable",
        "solverReady": solve_ready,
        "occBuilderReady": WAVEGUIDE_BUILDER_AVAILABLE and GMSH_OCC_RUNTIME_READY,
        "dependencies": dependency_status,
        "dependencyDoctor": {
            "schemaVersion": doctor_report.get("schemaVersion"),
            "generatedAt": doctor_report.get("generatedAt"),
            "platform": doctor_report.get("platform"),
            "summary": doctor_report.get("summary"),
            "components": doctor_report.get("components"),
            "solveReadiness": doctor_report.get("solveReadiness"),
        },
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
    workspace_subdir: str = Form(""),
    folder_path: Optional[str] = Form(None),
) -> Dict[str, str]:
    """
    Save an exported file to the backend-managed workspace root.
    Optional workspace_subdir writes into a nested folder under that root.
    """
    workspace_root = _get_default_output_path()

    # Backward-compatible alias while frontend migrates fully.
    requested_subdir = _coerce_form_string(workspace_subdir)
    if not requested_subdir:
        requested_subdir = _coerce_form_string(folder_path)

    if requested_subdir.startswith("/") or requested_subdir.startswith("\\"):
        raise HTTPException(status_code=400, detail="workspace_subdir must be relative to workspace root")

    subdir_parts = requested_subdir.replace("\\", "/").split("/")
    normalized_parts = [part.strip() for part in subdir_parts if part.strip()]
    if any(part in {".", ".."} for part in normalized_parts):
        raise HTTPException(status_code=400, detail="Invalid workspace_subdir")

    target_dir = (workspace_root / Path(*normalized_parts)).resolve()
    if workspace_root != target_dir and workspace_root not in target_dir.parents:
        raise HTTPException(status_code=400, detail="Invalid workspace_subdir")

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
            "filename": file.filename,
            "workspaceRoot": str(workspace_root),
            "workspaceSubdir": str(Path(*normalized_parts)) if normalized_parts else ""
        }
    except Exception as exc:
        logger.error(f"Export failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc


# ── Workspace path / open ──────────────────────────────────────────────────────

def _get_default_output_path() -> Path:
    """Return the absolute path of the default output folder (repo_root/output)."""
    # routes_misc.py lives at server/api/routes_misc.py → repo_root is three levels up
    return (Path(__file__).parent.parent.parent / "output").resolve()


@router.get("/api/workspace/path")
async def workspace_path() -> Dict[str, str]:
    """Return the absolute path of the current output folder."""
    output_path = _get_default_output_path()
    return {"path": str(output_path)}


@router.post("/api/workspace/open")
async def workspace_open() -> Dict[str, str]:
    """Open the output folder in the OS file manager."""
    output_path = _get_default_output_path()

    # Ensure the folder exists so the file manager can open it
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot create output folder: {exc}") from exc

    if not output_path.exists():
        raise HTTPException(status_code=404, detail=f"Output folder not found: {output_path}")

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(output_path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", str(output_path)])
        else:
            subprocess.Popen(["xdg-open", str(output_path)])
    except Exception as exc:
        logger.error(f"Failed to open folder in file manager: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}") from exc

    logger.info(f"Opened output folder in file manager: {output_path}")
    return {"status": "opened", "path": str(output_path)}
