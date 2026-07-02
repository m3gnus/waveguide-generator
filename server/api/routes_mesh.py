"""Mesh building routes for HornLab waveguide meshing."""

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from contracts import WaveguideParamsRequest
from services.gmsh_worker import run_on_gmsh_worker
from services.solver_runtime import (
    HORNLAB_MESHER_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    build_inner_surface_step,
    build_viewport_geometry,
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

    if request.formula_type not in ("R-OSSE", "OSSE", "ICW"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"formula_type '{request.formula_type}' is not supported. "
                "Supported types: 'R-OSSE', 'OSSE', 'ICW'."
            ),
        )

    try:
        payload = request.model_dump()
        # Multi-second gmsh build: the dedicated gmsh worker thread keeps the
        # event loop responsive. asyncio.to_thread is not safe here — gmsh
        # requires all calls on one persistent thread (services/gmsh_worker.py).
        result = await run_on_gmsh_worker(build_waveguide_mesh, payload)
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

    if request.formula_type not in ("R-OSSE", "OSSE", "ICW"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"formula_type '{request.formula_type}' is not supported. "
                "Supported types: 'R-OSSE', 'OSSE', 'ICW'."
            ),
        )
    if request.step_body != "inner_surface":
        raise HTTPException(
            status_code=422,
            detail=(
                f"step_body '{request.step_body}' is not supported. "
                "Supported STEP body: 'inner_surface'."
            ),
        )

    try:
        payload = request.model_dump()
        payload["step_body"] = "inner_surface"
        payload["quadrants"] = 1234
        payload["enc_depth"] = 0.0
        payload["wall_thickness"] = 0.0
        # gmsh-backed STEP export; same gmsh worker-thread contract as above.
        result = await run_on_gmsh_worker(build_inner_surface_step, payload)
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
async def build_viewport_geometry_from_params(request: WaveguideParamsRequest) -> Dict[str, Any]:
    """
    Serve fast viewport geometry: horn point grids plus enclosure profile rings.

    hornlab-waveguide-mesher owns the profile math; the browser owns the cheap
    display tessellation of the returned grids and rings. No Gmsh runs here, so
    this route does not require the gmsh runtime check and responds quickly
    enough for live parameter interaction.
    """
    if not HORNLAB_MESHER_AVAILABLE or build_viewport_geometry is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "hornlab-waveguide-mesher viewport geometry API is unavailable. "
                "Install server requirements to enable backend viewport geometry."
            ),
        )

    if request.formula_type not in ("R-OSSE", "OSSE", "ICW"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"formula_type '{request.formula_type}' is not supported. "
                "Supported types: 'R-OSSE', 'OSSE', 'ICW'."
            ),
        )

    try:
        payload = request.model_dump()
        payload["quadrants"] = 1234
        # Profile math is pure Python but not free: an ICW rollback homotopy
        # runs ~1 s, and the UI fires throttled requests during a drag.
        # Running it inline would block the event loop (job polling, other
        # viewport requests) behind every build; no gmsh runs here, so a
        # plain worker thread is safe.
        return await asyncio.to_thread(build_viewport_geometry, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Viewport geometry build failed: {exc}"
        ) from exc
