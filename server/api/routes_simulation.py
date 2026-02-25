"""
Simulation lifecycle routes: submit, status, results, jobs list, cancel, delete.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from models import (
    JobStatus,
    MeshData,
    SimulationRequest,
    WaveguideParamsRequest,
)
from solver_bootstrap import (
    SOLVER_AVAILABLE,
    BEMPP_RUNTIME_READY,
    WAVEGUIDE_BUILDER_AVAILABLE,
    GMSH_OCC_RUNTIME_READY,
    build_waveguide_mesh,
    normalize_mesh_validation_mode,
    get_dependency_status,
)
import services.job_runtime as _jrt
from services.job_runtime import (
    _build_config_summary,
    _drain_scheduler_queue,
    _merge_job_cache_from_db,
    _now_iso,
    _parse_status_filters,
    _remove_from_queue,
    _serialize_job_item,
    _set_job_fields,
    ensure_db_ready,
    job_queue,
    jobs,
    jobs_lock,
    running_jobs,
)
from services.simulation_runner import _validate_occ_adaptive_bem_shell

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/solve")
async def submit_simulation(request: SimulationRequest) -> Dict[str, str]:
    """Submit a new BEM simulation job. Returns a job ID for tracking progress."""
    triangle_count = len(request.mesh.indices) // 3
    if len(request.mesh.vertices) % 3 != 0:
        raise HTTPException(
            status_code=422, detail="Mesh vertices length must be divisible by 3."
        )
    if len(request.mesh.indices) % 3 != 0:
        raise HTTPException(
            status_code=422, detail="Mesh indices length must be divisible by 3."
        )
    if len(request.mesh.surfaceTags) != triangle_count:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Mesh surfaceTags length ({len(request.mesh.surfaceTags)}) "
                f"must match triangle count ({triangle_count})."
            ),
        )
    if str(request.sim_type).strip() != "2":
        raise HTTPException(
            status_code=422,
            detail=(
                "Only sim_type='2' (free-standing) is supported; "
                "infinite-baffle sim_type='1' was removed."
            ),
        )
    try:
        normalize_mesh_validation_mode(request.mesh_validation_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    options = request.options if isinstance(request.options, dict) else {}
    mesh_opts = (
        options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
    )
    mesh_strategy = str(mesh_opts.get("strategy", "")).strip().lower()

    if mesh_strategy == "occ_adaptive":
        waveguide_params = mesh_opts.get("waveguide_params")
        if not isinstance(waveguide_params, dict):
            raise HTTPException(
                status_code=422,
                detail=(
                    "options.mesh.waveguide_params must be an object when "
                    "options.mesh.strategy='occ_adaptive'."
                ),
            )
        try:
            validated_waveguide = WaveguideParamsRequest(**waveguide_params)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid options.mesh.waveguide_params: {exc.errors()}",
            ) from exc
        try:
            _validate_occ_adaptive_bem_shell(
                validated_waveguide.enc_depth,
                validated_waveguide.wall_thickness,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # BEM solve path always builds full-domain geometry.
        if int(validated_waveguide.quadrants) != 1234:
            waveguide_params["quadrants"] = 1234

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
            py_range = dep["supportedMatrix"].get("python", {}).get("range", ">=3.10,<3.14")
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
        bempp_legacy_range = dep["supportedMatrix"].get("bempp_api_legacy", {}).get(
            "range", ">=0.3,<0.4"
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "BEM solver not available. Please install bempp-cl. "
                f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                f"bempp variant={bempp_info.get('variant')} version={bempp_info.get('version')} "
                f"supported={bempp_info.get('supported')}. "
                f"Supported matrix: bempp-cl {bempp_cl_range}, legacy bempp_api {bempp_legacy_range}."
            ),
        )

    ensure_db_ready()
    job_id = str(uuid.uuid4())
    now = _now_iso()
    request_dump = request.model_dump()
    config_summary = _build_config_summary(request)

    job_record: Dict[str, Any] = {
        "id": job_id,
        "status": "queued",
        "progress": 0.0,
        "stage": "queued",
        "stage_message": "Job queued",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "error_message": None,
        "request": request_dump,
        "request_obj": request,
        "results": None,
        "mesh_artifact": None,
        "cancellation_requested": False,
        "config_summary": config_summary,
        "has_results": False,
        "has_mesh_artifact": False,
        "label": None,
    }

    _jrt.db.create_job(
        {
            "id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "progress": 0.0,
            "stage": "queued",
            "stage_message": "Job queued",
            "error_message": None,
            "cancellation_requested": False,
            "config_json": request_dump,
            "config_summary_json": config_summary,
            "has_results": False,
            "has_mesh_artifact": False,
            "label": None,
        }
    )

    with jobs_lock:
        jobs[job_id] = job_record
        job_queue.append(job_id)

    asyncio.create_task(_drain_scheduler_queue())

    return {"job_id": job_id}


@router.post("/api/stop/{job_id}")
async def stop_simulation(job_id: str) -> Dict[str, str]:
    """Stop a running simulation job."""
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] not in ["queued", "running"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot stop job with status: {job['status']}",
        )

    if job["status"] == "queued":
        _remove_from_queue(job_id)
        _set_job_fields(
            job_id,
            status="cancelled",
            progress=0.0,
            stage="cancelled",
            stage_message="Simulation cancelled",
            error_message="Simulation cancelled by user",
            completed_at=_now_iso(),
            cancellation_requested=True,
        )
    else:
        _set_job_fields(
            job_id,
            status="cancelled",
            stage="cancelled",
            stage_message="Simulation cancelled",
            error_message="Simulation cancelled by user",
            completed_at=_now_iso(),
            cancellation_requested=True,
        )

    asyncio.create_task(_drain_scheduler_queue())
    return {"message": f"Job {job_id} has been cancelled", "status": "cancelled"}


@router.get("/api/status/{job_id}")
async def get_job_status(job_id: str) -> JobStatus:
    """Get the status of a simulation job."""
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

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
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Job not complete. Current status: {job['status']}",
        )

    cached_results = job.get("results")
    if isinstance(cached_results, dict):
        return cached_results

    stored = _jrt.db.get_results(job_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Results not available")
    _set_job_fields(job_id, results=stored)
    return stored


@router.get("/api/mesh-artifact/{job_id}")
async def get_mesh_artifact(job_id: str) -> PlainTextResponse:
    """Download the simulation mesh artifact (.msh text) for a given job."""
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    msh_text = job.get("mesh_artifact")
    if not msh_text:
        msh_text = _jrt.db.get_mesh_artifact(job_id)
        if msh_text:
            _set_job_fields(job_id, mesh_artifact=msh_text, has_mesh_artifact=True)
    if not msh_text:
        raise HTTPException(status_code=404, detail="No mesh artifact available for this job")

    return PlainTextResponse(content=msh_text, media_type="text/plain")


@router.get("/api/jobs")
async def list_jobs(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    ensure_db_ready()
    statuses = _parse_status_filters(status)
    rows, total = _jrt.db.list_jobs(statuses=statuses, limit=limit, offset=offset)
    items: List[Dict[str, Any]] = []
    for row in rows:
        merged = _merge_job_cache_from_db(row["id"]) or row
        items.append(_serialize_job_item(merged))

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.delete("/api/jobs/clear-failed")
async def clear_failed_jobs() -> Dict[str, Any]:
    ensure_db_ready()
    deleted_ids = _jrt.db.delete_jobs_by_status(["error"])
    with jobs_lock:
        for job_id in deleted_ids:
            jobs.pop(job_id, None)
            running_jobs.discard(job_id)
    for job_id in deleted_ids:
        _remove_from_queue(job_id)
    return {
        "deleted": len(deleted_ids) > 0,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
    }


@router.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> Dict[str, Any]:
    ensure_db_ready()
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Cannot delete active job")

    deleted = _jrt.db.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")

    with jobs_lock:
        jobs.pop(job_id, None)
        running_jobs.discard(job_id)
    _remove_from_queue(job_id)
    return {"deleted": True, "job_id": job_id}
