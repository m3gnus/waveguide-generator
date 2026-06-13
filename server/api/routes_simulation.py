"""
Simulation lifecycle routes: submit, status, results, jobs list, cancel, delete.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from contracts import (
    JobMetadataPatch,
    JobStatus,
    MeshData,
    SimulationRequest,
)
from services.simulation_validation import (
    build_submit_simulation_request,
    is_hornlab_mesher_strategy,
    normalize_waveguide_params_for_solver_backend,
    validate_submit_simulation_request,
    SimulationRequestValidation,
)
from services.solver_runtime import (
    BEMPP_SOLVER_READY,
    HORNLAB_MESHER_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    bempp_backend_status,
    build_waveguide_mesh,
    get_dependency_status,
    is_metal_fast_solve_ready,
    metal_backend_status,
    metal_fast_solve_unavailable_reason,
    resolve_solver_backend,
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
    update_job_label,
    update_job_script_snapshot,
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
    requested_solver_backend = request.solver_backend
    solver_backend = resolve_solver_backend(
        request.solver_backend,
        mesh_strategy=validation.mesh_strategy,
    )
    validation = SimulationRequestValidation(
        mesh_strategy=validation.mesh_strategy,
        waveguide_params=normalize_waveguide_params_for_solver_backend(
            validation.waveguide_params,
            solver_backend,
        ),
    )
    request_to_submit = build_submit_simulation_request(request, validation)
    request_to_submit.solver_backend = solver_backend

    if is_hornlab_mesher_strategy(validation.mesh_strategy):
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

    if solver_backend == "metal":
        status = metal_backend_status()
        if not is_metal_fast_solve_ready(status):
            if requested_solver_backend == "auto" and not BEMPP_SOLVER_READY:
                bempp_status = bempp_backend_status()
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "No BEM solver backend is available. "
                        f"Metal: {metal_fast_solve_unavailable_reason(status) or 'unavailable'}; "
                        f"BEMPP: {bempp_status.get('reason') or 'unavailable'}. "
                        "Install the BEMPP fallback with: pip install -r server/requirements-bempp.txt"
                    ),
                )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Metal BEM solver not available. "
                    f"reason={metal_fast_solve_unavailable_reason(status)}; "
                    f"supported_platform={status.get('supportedPlatform')} "
                    f"native_helper_available={status.get('nativeHelperAvailable')}."
                ),
            )
    if solver_backend == "bempp" and not BEMPP_SOLVER_READY:
        status = bempp_backend_status()
        raise HTTPException(
            status_code=503,
            detail=(
                "BEMPP BEM solver not available. "
                f"reason={status.get('reason') or 'unavailable'}; "
                "install with: pip install -r server/requirements-bempp.txt."
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
        mesh_stats=job.get("mesh_stats"),
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


@router.patch("/api/jobs/{job_id}/metadata")
async def patch_job_metadata(job_id: str, body: JobMetadataPatch) -> Dict[str, str]:
    """Persist frontend-only metadata (label, script snapshot) so it survives page reloads."""
    try:
        changed_fields = body.model_fields_set
        if "label" in changed_fields:
            update_job_label(job_id, body.label)
        if "script_snapshot" in changed_fields:
            update_job_script_snapshot(job_id, body.script_snapshot)
    except JobRuntimeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return {"status": "ok"}


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
