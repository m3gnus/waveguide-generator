"""
Simulation lifecycle routes: submit, status, results, jobs list, cancel, delete.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from contracts import (
    JobStatus,
    MeshData,
    SimulationRequest,
)
from services.simulation_validation import (
    build_submit_simulation_request,
    validate_submit_simulation_request,
)
from services.solver_runtime import (
    SOLVER_AVAILABLE,
    BEMPP_RUNTIME_READY,
    WAVEGUIDE_BUILDER_AVAILABLE,
    GMSH_OCC_RUNTIME_READY,
    build_waveguide_mesh,
    get_dependency_status,
)
from services.job_runtime import (
    JobRuntimeConflictError,
    JobRuntimeNotFoundError,
    JobRuntimeResourceUnavailableError,
    clear_failed_job_records,
    create_simulation_job,
    delete_job_record,
    get_job,
    get_job_mesh_artifact,
    get_job_results,
    list_job_items,
    request_stop_job,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/solve")
async def submit_simulation(request: SimulationRequest) -> Dict[str, str]:
    """Submit a new BEM simulation job. Returns a job ID for tracking progress."""
    try:
        validation = validate_submit_simulation_request(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request_to_submit = build_submit_simulation_request(request, validation)

    if validation.mesh_strategy == "occ_adaptive":
        if not WAVEGUIDE_BUILDER_AVAILABLE or build_waveguide_mesh is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Python OCC mesh builder unavailable. "
                    "Install gmsh Python API: pip install 'gmsh>=4.11,<5.0'"
                ),
            )

        if not GMSH_OCC_RUNTIME_READY:
            dep = get_dependency_status()
            gmsh_info = dep["runtime"]["gmsh_python"]
            py_info = dep["runtime"]["python"]
            gmsh_range = dep["supportedMatrix"].get("gmsh_python", {}).get("range", ">=4.11,<5.0")
            py_range = dep["supportedMatrix"].get("python", {}).get("range", ">=3.10,<3.15")
            raise HTTPException(
                status_code=503,
                detail=(
                    "Adaptive OCC mesh builder dependency check failed. "
                    f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                    f"gmsh={gmsh_info.get('version')} supported={gmsh_info.get('supported')}. "
                    f"Supported matrix: python {py_range}, gmsh {gmsh_range}."
                ),
            )

    if not SOLVER_AVAILABLE:
        dep = get_dependency_status()
        bempp_info = dep["runtime"]["bempp"]
        py_info = dep["runtime"]["python"]
        bempp_cl_range = dep["supportedMatrix"].get("bempp_cl", {}).get("range", ">=0.4,<0.5")
        raise HTTPException(
            status_code=503,
            detail=(
                "BEM solver not available. Please install bempp-cl. "
                f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                f"bempp variant={bempp_info.get('variant')} version={bempp_info.get('version')} "
                f"supported={bempp_info.get('supported')}. "
                f"Supported matrix: bempp-cl {bempp_cl_range}."
            ),
        )

    job_id = create_simulation_job(request_to_submit)

    return {"job_id": job_id}


@router.post("/api/stop/{job_id}")
async def stop_simulation(job_id: str) -> Dict[str, str]:
    """Stop a running simulation job."""
    try:
        return request_stop_job(job_id)
    except JobRuntimeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except JobRuntimeConflictError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get("/api/status/{job_id}")
async def get_job_status(job_id: str) -> JobStatus:
    """Get the status of a simulation job."""
    try:
        job = get_job(job_id)
    except JobRuntimeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc

    return JobStatus(
        status=job["status"],
        progress=float(job.get("progress", 0.0)),
        stage=job.get("stage"),
        stage_message=job.get("stage_message"),
        message=job.get("error_message") or job.get("error"),
    )


@router.get("/api/results/{job_id}")
async def get_results(job_id: str) -> Dict[str, Any]:
    """Retrieve simulation results."""
    try:
        return get_job_results(job_id)
    except JobRuntimeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except JobRuntimeConflictError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JobRuntimeResourceUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/mesh-artifact/{job_id}")
async def get_mesh_artifact(job_id: str) -> PlainTextResponse:
    """Download the simulation mesh artifact (.msh text) for a given job."""
    try:
        msh_text = get_job_mesh_artifact(job_id)
    except JobRuntimeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except JobRuntimeResourceUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PlainTextResponse(content=msh_text, media_type="text/plain")


@router.get("/api/jobs")
async def list_jobs(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    items, total = list_job_items(status=status, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.delete("/api/jobs/clear-failed")
async def clear_failed_jobs() -> Dict[str, Any]:
    deleted_ids = clear_failed_job_records()
    return {
        "deleted": len(deleted_ids) > 0,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
    }


@router.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> Dict[str, Any]:
    try:
        delete_job_record(job_id)
    except JobRuntimeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except JobRuntimeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": True, "job_id": job_id}
