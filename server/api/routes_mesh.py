"""Mesh building routes for HornLab waveguide meshing."""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from contracts import WaveguideParamsRequest
from services.solver_runtime import (
    HORNLAB_MESHER_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    build_inner_surface_step,
    build_viewport_mesh,
    build_waveguide_mesh,
    get_dependency_status,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/mesh/build")
async def build_mesh_from_params(request: WaveguideParamsRequest) -> Dict[str, Any]:
    """
    Build a tagged Gmsh .msh from ATH waveguide parameters using hornlab-waveguide-mesher.
    Returns 503 if the mesher dependency is not available.
    """
    if not HORNLAB_MESHER_AVAILABLE or build_waveguide_mesh is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "hornlab-waveguide-mesher is unavailable. "
                "Install server requirements to enable backend mesh generation."
            ),
        )

    if not HORNLAB_MESHER_RUNTIME_READY:
        dep = get_dependency_status()
        gmsh_info = dep["runtime"]["gmsh_python"]
        py_info = dep["runtime"]["python"]
        gmsh_range = dep["supportedMatrix"].get("gmsh_python", {}).get("range", ">=4.11,<5.0")
        py_range = dep["supportedMatrix"].get("python", {}).get("range", ">=3.10,<3.15")
        raise HTTPException(
            status_code=503,
            detail=(
                "hornlab-waveguide-mesher dependency check failed. "
                f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                f"gmsh={gmsh_info.get('version')} supported={gmsh_info.get('supported')}. "
                f"Supported matrix: python {py_range}, gmsh {gmsh_range}."
            ),
        )

    if request.msh_version not in ("2.2", "4.1"):
        raise HTTPException(status_code=422, detail="msh_version must be '2.2' or '4.1'.")

    if request.formula_type not in ("R-OSSE", "OSSE"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"formula_type '{request.formula_type}' is not supported. "
                "Supported types: 'R-OSSE', 'OSSE'."
            ),
        )

    try:
        payload = request.model_dump()
        # Run directly on the request thread — gmsh Python API fails in worker threads.
        result = build_waveguide_mesh(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Waveguide build failed: {exc}"
        ) from exc

    response = {
        "msh": result["msh_text"],
        "generatedBy": "hornlab-waveguide-mesher",
        "stats": result["stats"],
    }
    if result.get("stl_text"):
        response["stl"] = result["stl_text"]
    return response


@router.post("/api/mesh/step")
async def build_step_from_params(request: WaveguideParamsRequest) -> Dict[str, Any]:
    """
    Build a single-layer STEP surface from ATH waveguide parameters.

    The output intentionally includes only the acoustic inner horn surface. It
    excludes wall thickness, source/rear caps, and enclosure geometry so CAD
    users can thicken/enclose the model later in SolidWorks.
    """
    if not HORNLAB_MESHER_AVAILABLE or build_inner_surface_step is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "hornlab-waveguide-mesher STEP export is unavailable. "
                "Install server requirements to enable backend STEP generation."
            ),
        )

    if not HORNLAB_MESHER_RUNTIME_READY:
        dep = get_dependency_status()
        gmsh_info = dep["runtime"]["gmsh_python"]
        py_info = dep["runtime"]["python"]
        gmsh_range = dep["supportedMatrix"].get("gmsh_python", {}).get("range", ">=4.11,<5.0")
        py_range = dep["supportedMatrix"].get("python", {}).get("range", ">=3.10,<3.15")
        raise HTTPException(
            status_code=503,
            detail=(
                "hornlab-waveguide-mesher dependency check failed. "
                f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                f"gmsh={gmsh_info.get('version')} supported={gmsh_info.get('supported')}. "
                f"Supported matrix: python {py_range}, gmsh {gmsh_range}."
            ),
        )

    if request.formula_type not in ("R-OSSE", "OSSE"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"formula_type '{request.formula_type}' is not supported. "
                "Supported types: 'R-OSSE', 'OSSE'."
            ),
        )

    try:
        payload = request.model_dump()
        payload["quadrants"] = 1234
        payload["enc_depth"] = 0.0
        payload["wall_thickness"] = 0.0
        result = build_inner_surface_step(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"STEP export failed: {exc}"
        ) from exc

    return {
        "step": result["step_text"],
        "generatedBy": "hornlab-waveguide-mesher",
        "stats": result["stats"],
    }


@router.post("/api/mesh/viewport")
async def build_viewport_mesh_from_params(request: WaveguideParamsRequest) -> Dict[str, Any]:
    """
    Build viewport geometry from hornlab-waveguide-mesher Gmsh output.

    The browser receives a display mesh converted to millimetres, while the
    backend mesher owns both surface construction and triangle tessellation.
    """
    if not HORNLAB_MESHER_AVAILABLE or build_viewport_mesh is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "hornlab-waveguide-mesher viewport mesh API is unavailable. "
                "Install server requirements to enable backend viewport geometry."
            ),
        )

    if not HORNLAB_MESHER_RUNTIME_READY:
        dep = get_dependency_status()
        gmsh_info = dep["runtime"]["gmsh_python"]
        py_info = dep["runtime"]["python"]
        gmsh_range = dep["supportedMatrix"].get("gmsh_python", {}).get("range", ">=4.11,<5.0")
        py_range = dep["supportedMatrix"].get("python", {}).get("range", ">=3.10,<3.15")
        raise HTTPException(
            status_code=503,
            detail=(
                "hornlab-waveguide-mesher dependency check failed. "
                f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                f"gmsh={gmsh_info.get('version')} supported={gmsh_info.get('supported')}. "
                f"Supported matrix: python {py_range}, gmsh {gmsh_range}."
            ),
        )

    try:
        payload = request.model_dump()
        payload["quadrants"] = 1234
        return build_viewport_mesh(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Viewport mesh build failed: {exc}"
        ) from exc
