"""
Job runtime state, scheduler, and DB helper functions.

Owns the in-memory job cache, queue, and the FIFO scheduler that drains the
queue one job at a time (max_concurrent_jobs=1).
"""

import asyncio
import json
import logging
import multiprocessing
import threading
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from db import SimulationDB
from contracts import SimulationRequest

logger = logging.getLogger(__name__)

# ── Global runtime state ───────────────────────────────────────────────────────
jobs: Dict[str, Dict[str, Any]] = {}
job_queue: deque[str] = deque()
running_jobs: set[str] = set()
# Subprocess references for hard-kill cancellation.  Maps job_id → Process.
running_processes: Dict[str, multiprocessing.Process] = {}
jobs_lock = threading.RLock()
scheduler_loop_running: bool = False
max_concurrent_jobs: int = 1
# Python 3.12+ only keeps weak references to asyncio tasks; prevent GC of
# fire-and-forget tasks by holding strong references here.
_background_tasks: set = set()

db = SimulationDB(Path(__file__).resolve().parents[1] / "data" / "simulations.db")
db_initialized: bool = False


def _keep_task(task: "asyncio.Task[Any]") -> None:
    """Hold a strong reference to *task* so the GC cannot collect it (Python 3.12+)."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


class JobRuntimeNotFoundError(LookupError):
    """Requested job does not exist in runtime cache or persistence."""


class JobRuntimeConflictError(RuntimeError):
    """Requested job action is invalid for the current job state."""


class JobRuntimeResourceUnavailableError(RuntimeError):
    """Requested persisted job resource is not available."""


# ── DB readiness ───────────────────────────────────────────────────────────────

def ensure_db_ready() -> None:
    global db_initialized
    if db_initialized:
        return
    db.initialize()
    db_initialized = True


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _is_terminal_status(status: str) -> bool:
    return status in {"complete", "error", "cancelled"}


def _now_iso() -> str:
    return datetime.now().isoformat()


def _build_config_summary(request: SimulationRequest) -> Dict[str, Any]:
    options = request.options if isinstance(request.options, dict) else {}
    mesh_opts = options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
    waveguide_params = (
        mesh_opts.get("waveguide_params")
        if isinstance(mesh_opts.get("waveguide_params"), dict)
        else {}
    )
    return {
        "formula_type": waveguide_params.get("formula_type"),
        "frequency_range": request.frequency_range,
        "num_frequencies": request.num_frequencies,
        "sim_type": str(request.sim_type),
    }


def create_simulation_job(request: SimulationRequest) -> str:
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
        "mesh_stats": None,
        "cancellation_requested": False,
        "config_summary": config_summary,
        "has_results": False,
        "has_mesh_artifact": False,
        "label": None,
    }

    db.create_job(
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
            "mesh_stats": None,
            "label": None,
        }
    )

    with jobs_lock:
        jobs[job_id] = job_record
        job_queue.append(job_id)

    _keep_task(asyncio.create_task(_drain_scheduler_queue()))
    return job_id


def get_job(job_id: str) -> Dict[str, Any]:
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise JobRuntimeNotFoundError(job_id)
    return job


def request_stop_job(job_id: str) -> Dict[str, str]:
    from services.simulation_runner import (  # noqa: PLC0415
        CANCELLATION_REQUESTED_MESSAGE,
        SIMULATION_CANCELLED_MESSAGE,
    )

    job = get_job(job_id)

    if job["status"] not in ["queued", "running"]:
        raise JobRuntimeConflictError(f"Cannot stop job with status: {job['status']}")

    if job["status"] == "queued":
        _remove_from_queue(job_id)
        _set_job_fields(
            job_id,
            status="cancelled",
            progress=0.0,
            stage="cancelled",
            stage_message="Simulation cancelled",
            error_message=SIMULATION_CANCELLED_MESSAGE,
            completed_at=_now_iso(),
            cancellation_requested=False,
        )
        response = {
            "message": f"Job {job_id} has been cancelled",
            "status": "cancelled",
        }
    else:
        # Hard-kill the solver subprocess if one is running.
        _terminate_solver_process(job_id)
        _set_job_fields(
            job_id,
            stage="cancelling",
            stage_message=CANCELLATION_REQUESTED_MESSAGE,
            cancellation_requested=True,
        )
        response = {
            "message": f"Cancellation requested for job {job_id}",
            "status": "cancelling",
        }

    _keep_task(asyncio.create_task(_drain_scheduler_queue()))
    return response


def get_job_results(job_id: str) -> Dict[str, Any]:
    job = get_job(job_id)
    if job["status"] != "complete":
        raise JobRuntimeConflictError(f"Job not complete. Current status: {job['status']}")

    cached_results = job.get("results")
    if isinstance(cached_results, dict):
        return cached_results

    stored = db.get_results(job_id)
    if stored is None:
        raise JobRuntimeResourceUnavailableError("Results not available")
    _set_job_fields(job_id, results=stored)
    return stored


def get_job_mesh_artifact(job_id: str) -> str:
    job = get_job(job_id)
    msh_text = job.get("mesh_artifact")
    if not msh_text:
        msh_text = db.get_mesh_artifact(job_id)
        if msh_text:
            _set_job_fields(job_id, mesh_artifact=msh_text, has_mesh_artifact=True)
    if not msh_text:
        raise JobRuntimeResourceUnavailableError("No mesh artifact available for this job")
    return msh_text


def list_job_items(
    *,
    status: Optional[str],
    limit: int,
    offset: int,
) -> Tuple[List[Dict[str, Any]], int]:
    ensure_db_ready()
    statuses = _parse_status_filters(status)
    rows, total = db.list_jobs(statuses=statuses, limit=limit, offset=offset)
    items: List[Dict[str, Any]] = []
    for row in rows:
        merged = _merge_job_cache_from_db(row["id"]) or row
        items.append(_serialize_job_item(merged))
    return items, total


def clear_failed_job_records() -> List[str]:
    ensure_db_ready()
    deleted_ids = db.delete_jobs_by_status(["error"])
    with jobs_lock:
        for job_id in deleted_ids:
            jobs.pop(job_id, None)
            running_jobs.discard(job_id)
    for job_id in deleted_ids:
        _remove_from_queue(job_id)
    return deleted_ids


def delete_job_record(job_id: str) -> None:
    ensure_db_ready()
    job = get_job(job_id)
    if job.get("status") in {"queued", "running"}:
        raise JobRuntimeConflictError("Cannot delete active job")

    deleted = db.delete_job(job_id)
    if not deleted:
        raise JobRuntimeNotFoundError(job_id)

    with jobs_lock:
        jobs.pop(job_id, None)
        running_jobs.discard(job_id)
    _remove_from_queue(job_id)


# ── Job cache + DB merge ───────────────────────────────────────────────────────

def _merge_job_cache_from_db(job_id: str) -> Optional[Dict[str, Any]]:
    ensure_db_ready()
    with jobs_lock:
        cached = jobs.get(job_id)
        if cached:
            return cached
    row = db.get_job_row(job_id)
    if not row:
        return None
    merged: Dict[str, Any] = {
        "id": row["id"],
        "status": row["status"],
        "progress": row["progress"],
        "stage": row.get("stage"),
        "stage_message": row.get("stage_message"),
        "created_at": row.get("created_at"),
        "queued_at": row.get("queued_at"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "error": row.get("error_message"),
        "error_message": row.get("error_message"),
        "has_results": row.get("has_results"),
        "has_mesh_artifact": row.get("has_mesh_artifact"),
        "mesh_stats": row.get("mesh_stats"),
        "label": row.get("label"),
        "cancellation_requested": row.get("cancellation_requested"),
        "config_summary": row.get("config_summary_json"),
    }
    config = row.get("config_json")
    if isinstance(config, dict):
        merged["request"] = config
        try:
            merged["request_obj"] = SimulationRequest(**config)
        except Exception as _exc:
            logger.debug(
                "Could not reconstruct SimulationRequest for job %s: %s", job_id, _exc
            )
    with jobs_lock:
        jobs[job_id] = merged
    return merged


def _set_job_fields(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    ensure_db_ready()
    if not fields:
        return _merge_job_cache_from_db(job_id)

    if "progress" in fields:
        fields["progress"] = max(0.0, min(1.0, float(fields["progress"])))

    now = _now_iso()
    fields.setdefault("updated_at", now)
    mapped = dict(fields)
    if "error" in mapped and "error_message" not in mapped:
        mapped["error_message"] = mapped["error"]

    db_fields = {
        key: mapped[key]
        for key in [
            "status",
            "progress",
            "stage",
            "stage_message",
            "error_message",
            "started_at",
            "completed_at",
            "cancellation_requested",
            "has_results",
            "has_mesh_artifact",
            "label",
        ]
        if key in mapped
    }
    if "mesh_stats" in mapped:
        db_fields["mesh_stats_json"] = (
            json.dumps(mapped["mesh_stats"]) if mapped["mesh_stats"] is not None else None
        )
    if "script_snapshot" in mapped:
        db_fields["script_snapshot_json"] = (
            json.dumps(mapped["script_snapshot"]) if mapped["script_snapshot"] is not None else None
        )
    if db_fields:
        db.update_job(job_id, **db_fields)

    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            job.update(mapped)
            if "error_message" in mapped:
                job["error"] = mapped["error_message"]
            return job
    return _merge_job_cache_from_db(job_id)


def update_job_label(job_id: str, label: Any) -> None:
    """Persist a display label for a job."""
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise JobRuntimeNotFoundError(job_id)
    _set_job_fields(job_id, label=str(label) if label else None)


def update_job_script_snapshot(job_id: str, script_snapshot: Any) -> None:
    """Persist a script snapshot (UI parameters) for a job."""
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise JobRuntimeNotFoundError(job_id)
    _set_job_fields(job_id, script_snapshot=script_snapshot)


def update_progress(job_id: str, progress: float) -> None:
    job = _merge_job_cache_from_db(job_id)
    if not job:
        return
    if _is_terminal_status(job.get("status", "")):
        return
    _set_job_fields(job_id, progress=progress)


def update_job_stage(
    job_id: str,
    stage: str,
    *,
    progress: Optional[float] = None,
    stage_message: Optional[str] = None,
) -> None:
    """Update non-terminal job stage metadata."""
    job = _merge_job_cache_from_db(job_id)
    if not job:
        return
    if _is_terminal_status(job.get("status", "")):
        return
    payload: Dict[str, Any] = {"stage": stage}
    if stage_message is not None:
        payload["stage_message"] = stage_message
    if progress is not None:
        payload["progress"] = progress
    _set_job_fields(job_id, **payload)


def register_solver_process(job_id: str, process: multiprocessing.Process) -> None:
    """Store a reference to the solver subprocess for hard-kill support."""
    with jobs_lock:
        running_processes[job_id] = process


def unregister_solver_process(job_id: str) -> None:
    """Remove the solver subprocess reference after it finishes."""
    with jobs_lock:
        running_processes.pop(job_id, None)


def _terminate_solver_process(job_id: str) -> None:
    """Send SIGTERM to the solver subprocess, then SIGKILL if it doesn't exit."""
    with jobs_lock:
        proc = running_processes.get(job_id)
    if proc is None or not proc.is_alive():
        return
    logger.info("Hard-killing solver subprocess for job %s (pid=%s)", job_id, proc.pid)
    proc.terminate()
    proc.join(timeout=5)
    if proc.is_alive():
        logger.warning("Subprocess for job %s did not exit after SIGTERM; sending SIGKILL", job_id)
        proc.kill()
        proc.join(timeout=3)
    unregister_solver_process(job_id)


def _remove_from_queue(job_id: str) -> None:
    with jobs_lock:
        if not job_queue:
            return
        remaining = [queued_id for queued_id in job_queue if queued_id != job_id]
        job_queue.clear()
        job_queue.extend(remaining)


# ── FIFO scheduler ─────────────────────────────────────────────────────────────

async def _drain_scheduler_queue() -> None:
    global scheduler_loop_running
    with jobs_lock:
        if scheduler_loop_running:
            return
        scheduler_loop_running = True
    try:
        while True:
            with jobs_lock:
                can_start = (
                    len(running_jobs) < max_concurrent_jobs and len(job_queue) > 0
                )
                if not can_start:
                    break
                job_id = job_queue.popleft()
                running_jobs.add(job_id)

            job = _merge_job_cache_from_db(job_id)
            if not job:
                with jobs_lock:
                    running_jobs.discard(job_id)
                continue
            if job.get("status") != "queued":
                with jobs_lock:
                    running_jobs.discard(job_id)
                continue

            started_at = _now_iso()
            _set_job_fields(
                job_id,
                status="running",
                started_at=started_at,
                stage="initializing",
                stage_message="Initializing BEM solver",
                progress=0.05,
            )

            request_obj = job.get("request_obj")
            if request_obj is None:
                raw = job.get("request")
                if not isinstance(raw, dict):
                    with jobs_lock:
                        running_jobs.discard(job_id)
                    _set_job_fields(
                        job_id,
                        status="error",
                        stage="error",
                        stage_message="Simulation failed",
                        error_message="Unable to restore queued simulation payload.",
                        completed_at=_now_iso(),
                    )
                    continue
                try:
                    request_obj = SimulationRequest(**raw)
                except Exception as exc:
                    with jobs_lock:
                        running_jobs.discard(job_id)
                    _set_job_fields(
                        job_id,
                        status="error",
                        stage="error",
                        stage_message="Simulation failed",
                        error_message=f"Unable to validate restored simulation payload: {exc}",
                        completed_at=_now_iso(),
                    )
                    continue
                _set_job_fields(job_id, request_obj=request_obj)

            # Lazy import avoids the circular dependency:
            # simulation_runner → job_runtime → simulation_runner
            from services.simulation_runner import run_simulation  # noqa: PLC0415
            _keep_task(asyncio.create_task(run_simulation(job_id, request_obj)))
    finally:
        with jobs_lock:
            scheduler_loop_running = False


# ── List / serialisation helpers ───────────────────────────────────────────────

def _serialize_job_item(job: Dict[str, Any]) -> Dict[str, Any]:
    summary = job.get("config_summary")
    if summary is None:
        summary = job.get("config_summary_json", {})
    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "progress": float(job.get("progress", 0.0)),
        "stage": job.get("stage"),
        "stage_message": job.get("stage_message"),
        "created_at": job.get("created_at"),
        "queued_at": job.get("queued_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "config_summary": summary or {},
        "has_results": bool(job.get("has_results")),
        "has_mesh_artifact": bool(job.get("has_mesh_artifact")),
        "label": job.get("label"),
        "error_message": job.get("error_message"),
        "cancellation_requested": bool(job.get("cancellation_requested")),
        "mesh_stats": job.get("mesh_stats"),
        "script_snapshot": job.get("script_snapshot"),
    }


def _parse_status_filters(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    values = [token.strip() for token in str(raw).split(",") if token.strip()]
    if not values:
        return None
    allowed = {"queued", "running", "complete", "error", "cancelled"}
    bad = [value for value in values if value not in allowed]
    if bad:
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(
            status_code=422,
            detail=f"status filter contains unsupported values: {', '.join(bad)}",
        )
    dedup: List[str] = []
    for value in values:
        if value not in dedup:
            dedup.append(value)
    return dedup


# ── Startup recovery ───────────────────────────────────────────────────────────

async def startup_jobs_runtime() -> None:
    ensure_db_ready()
    db.prune_terminal_jobs(retention_days=30, max_terminal_jobs=1000)

    queued_rows = db.recover_on_startup("Server restarted during execution")
    should_schedule = False
    with jobs_lock:
        queued_job_ids = set(job_queue)
        for row in queued_rows:
            request_obj = None
            request_dump = row.get("config_json")
            if isinstance(request_dump, dict):
                try:
                    request_obj = SimulationRequest(**request_dump)
                except Exception:
                    request_obj = None
            jobs[row["id"]] = {
                "id": row["id"],
                "status": row["status"],
                "progress": row["progress"],
                "stage": row.get("stage"),
                "stage_message": row.get("stage_message"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "queued_at": row.get("queued_at"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "request": request_dump,
                "request_obj": request_obj,
                "error_message": row.get("error_message"),
                "error": row.get("error_message"),
                "results": None,
                "mesh_artifact": None,
                "mesh_stats": row.get("mesh_stats"),
                "cancellation_requested": row.get("cancellation_requested", False),
                "config_summary": row.get("config_summary_json", {}),
                "has_results": row.get("has_results", False),
                "has_mesh_artifact": row.get("has_mesh_artifact", False),
                "label": row.get("label"),
            }
            if row["id"] not in queued_job_ids:
                job_queue.append(row["id"])
                queued_job_ids.add(row["id"])
                should_schedule = True

    if should_schedule:
        _keep_task(asyncio.create_task(_drain_scheduler_queue()))
