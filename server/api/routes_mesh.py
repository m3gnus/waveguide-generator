"""Mesh building routes for OCC waveguide meshing."""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from models import WaveguideParamsRequest
from solver_bootstrap import (
    WAVEGUIDE_BUILDER_AVAILABLE,
    GMSH_OCC_RUNTIME_READY,
    build_waveguide_mesh,
    get_dependency_status,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/mesh/build")
async def build_mesh_from_params(request: WaveguideParamsRequest) -> Dict[str, Any]:
    """
    Build a Gmsh-authored .msh from ATH waveguide parameters using the Gmsh OCC Python API.
    Returns 503 if the Gmsh Python API is not available.
    """
    if not WAVEGUIDE_BUILDER_AVAILABLE or build_waveguide_mesh is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Python OCC mesh builder unavailable. "
                "Install gmsh Python API: pip install gmsh>=4.15.0"
            ),
        )

    if not GMSH_OCC_RUNTIME_READY:
        dep = get_dependency_status()
        gmsh_info = dep["runtime"]["gmsh_python"]
        py_info = dep["runtime"]["python"]
        gmsh_range = dep["supportedMatrix"].get("gmsh_python", {}).get("range", ">=4.15,<5.0")
        py_range = dep["supportedMatrix"].get("python", {}).get("range", ">=3.10,<3.15")
        raise HTTPException(
            status_code=503,
            detail=(
                "Python OCC mesh builder dependency check failed. "
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
        # Run directly on the request thread — gmsh Python API fails in worker threads.
        result = build_waveguide_mesh(request.model_dump())
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
        "generatedBy": "gmsh-occ",
        "stats": result["stats"],
    }
    if result.get("stl_text"):
        response["stl"] = result["stl_text"]
    return response
