"""
BEM simulation runner — executes a single simulation job asynchronously.
"""

import asyncio
import json
import logging
import multiprocessing as mp
import tempfile
from pathlib import Path
from queue import Empty as QueueEmpty
from typing import Any, Callable, Optional

from contracts import SimulationRequest, WaveguideParamsRequest
from services.simulation_validation import validate_occ_adaptive_bem_shell
from services.solver_runtime import (
    BEMSolver,
    WAVEGUIDE_BUILDER_AVAILABLE,
    GMSH_OCC_RUNTIME_READY,
    build_waveguide_mesh,
)
from services.job_runtime import (
    _merge_job_cache_from_db,
    _set_job_fields,
    _now_iso,
    _keep_task,
    update_job_stage,
    register_solver_process,
    unregister_solver_process,
    running_jobs,
    jobs_lock,
    db,
    _drain_scheduler_queue,
)

logger = logging.getLogger(__name__)
CANONICAL_SURFACE_TAGS = {1, 2, 3, 4}
CANCELLATION_REQUESTED_MESSAGE = "Cancellation requested; waiting for backend worker to stop"
SIMULATION_CANCELLED_MESSAGE = "Simulation cancelled by user"


class SimulationCancelled(RuntimeError):
    """Raised when a queued/running job acknowledges a stop request."""


def _is_cancellation_requested(job_id: str) -> bool:
    latest = _merge_job_cache_from_db(job_id)
    return bool(latest and latest.get("cancellation_requested"))


def _raise_if_cancellation_requested(
    job_id: str,
    *,
    stage_message: str = CANCELLATION_REQUESTED_MESSAGE,
) -> None:
    if not _is_cancellation_requested(job_id):
        return
    update_job_stage(job_id, "cancelling", stage_message=stage_message)
    raise SimulationCancelled(SIMULATION_CANCELLED_MESSAGE)


def _finalize_cancelled_job(
    job_id: str,
    *,
    stage_message: str = SIMULATION_CANCELLED_MESSAGE,
) -> None:
    _set_job_fields(
        job_id,
        status="cancelled",
        stage="cancelled",
        stage_message=stage_message,
        error_message=SIMULATION_CANCELLED_MESSAGE,
        completed_at=_now_iso(),
        cancellation_requested=False,
    )


def _extract_occ_adaptive_canonical_mesh(
    occ_result: dict[str, Any],
) -> tuple[list[Any], list[Any], list[int]]:
    canonical = occ_result.get("canonical_mesh") or {}
    vertices = canonical.get("vertices")
    indices = canonical.get("indices")
    surface_tags = canonical.get("surfaceTags")
    if (
        not isinstance(vertices, list)
        or not isinstance(indices, list)
        or not isinstance(surface_tags, list)
    ):
        raise RuntimeError(
            "Adaptive OCC mesh generation did not return canonical mesh arrays."
        )
    if len(indices) % 3 != 0:
        raise RuntimeError("Adaptive OCC mesh returned invalid triangle index data.")
    if len(surface_tags) != len(indices) // 3:
        raise RuntimeError("Adaptive OCC mesh returned mismatched surface tag count.")

    normalized_surface_tags = [int(tag) for tag in surface_tags]
    invalid_tags = sorted({tag for tag in normalized_surface_tags if tag not in CANONICAL_SURFACE_TAGS})
    if invalid_tags:
        raise RuntimeError(
            f"Adaptive OCC mesh returned unsupported surface tags: {invalid_tags}."
        )
    if 2 not in normalized_surface_tags:
        raise RuntimeError("Adaptive OCC mesh returned no source-tagged elements (tag 2).")
    return vertices, indices, normalized_surface_tags


def _build_mesh_stats(
    vertices: list[Any],
    indices: list[Any],
    *,
    source: str,
    surface_tags: Optional[list[int]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    mesh_stats = {
        "vertex_count": len(vertices) // 3,
        "triangle_count": len(indices) // 3,
        "source": source,
    }
    if isinstance(surface_tags, list):
        tag_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for raw_tag in surface_tags:
            tag = int(raw_tag)
            if tag in tag_counts:
                tag_counts[tag] += 1
        mesh_stats["tag_counts"] = tag_counts
    metadata_identity_counts = (
        metadata.get("identityTriangleCounts")
        if isinstance(metadata, dict)
        else None
    )
    if isinstance(metadata_identity_counts, dict):
        mesh_stats["identity_triangle_counts"] = json.loads(
            json.dumps(metadata_identity_counts)
        )
    return mesh_stats


def _apply_solver_stage_to_job(
    job_id: str, stage: str, progress: Optional[float], message: Optional[str]
) -> None:
    """Map solver stage names to job stage/progress ranges (same logic as the old callback)."""
    normalized_progress = 0.0 if progress is None else max(0.0, min(1.0, float(progress)))

    if stage in {"setup", "solver_setup"}:
        update_job_stage(
            job_id, "bem_solve",
            progress=0.30 + (normalized_progress * 0.05),
            stage_message=message or "Configuring BEM solve",
        )
    elif stage == "frequency_solve":
        update_job_stage(
            job_id, "bem_solve",
            progress=0.35 + (normalized_progress * 0.50),
            stage_message=message or "Solving BEM frequencies",
        )
    elif stage == "directivity":
        update_job_stage(
            job_id, "finalizing",
            progress=0.85 + (normalized_progress * 0.13),
            stage_message=message or "Generating requested polar maps and deriving DI from solved frequencies",
        )
    elif stage == "finalizing":
        update_job_stage(
            job_id, "finalizing",
            progress=0.98 + (normalized_progress * 0.01),
            stage_message=message or "Finalizing results",
        )
    else:
        update_job_stage(job_id, str(stage), stage_message=message)


_SUBPROCESS_QUEUE_POLL_SECONDS = 0.5


async def _run_solve_in_subprocess(
    job_id: str, mesh: Any, request: "SimulationRequest"
) -> dict:
    """
    Spawn a child process to run the BEM solve, monitor its IPC queue for
    progress/stage updates, and hard-kill it on cancellation.
    """
    from solver.solve import _serialize_mesh_for_subprocess, _solve_subprocess_worker

    serialized_mesh = _serialize_mesh_for_subprocess(mesh)

    # Build the kwargs dict that will be forwarded to solve_optimized() inside the child.
    # Callbacks are set inside the worker; we only pass serializable scalars here.
    advanced = (
        request.advanced_settings.model_dump(exclude_none=True)
        if request.advanced_settings else None
    )
    solve_kwargs: dict = {
        "frequency_range": list(request.frequency_range),
        "num_frequencies": request.num_frequencies,
        "sim_type": request.sim_type,
        "polar_config": request.polar_config.model_dump() if request.polar_config else None,
        "verbose": request.verbose,
        "mesh_validation_mode": request.mesh_validation_mode,
        "frequency_spacing": request.frequency_spacing,
        "device_mode": request.device_mode,
    }
    if advanced:
        # Unpack stable runtime settings.
        _STABLE = {"use_burton_miller", "quadrature_regular", "workgroup_size_multiple", "assembly_backend"}
        for key in list(advanced.keys()):
            if key in _STABLE:
                solve_kwargs[key] = advanced[key]

    ctx = mp.get_context("spawn")
    progress_queue = ctx.Queue()
    cancel_event = ctx.Event()

    proc = ctx.Process(
        target=_solve_subprocess_worker,
        args=(serialized_mesh, solve_kwargs, progress_queue, cancel_event),
        daemon=True,
    )
    proc.start()
    register_solver_process(job_id, proc)
    logger.info("Solver subprocess started for job %s (pid=%s)", job_id, proc.pid)

    try:
        return await _monitor_solver_subprocess(job_id, proc, progress_queue, cancel_event)
    finally:
        unregister_solver_process(job_id)
        # Ensure the child process is cleaned up.
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)


async def _monitor_solver_subprocess(
    job_id: str,
    proc: "mp.Process",
    progress_queue: "mp.Queue",
    cancel_event: "mp.Event",
) -> dict:
    """
    Drain IPC messages from the solver subprocess and update job state.

    Returns the results dict on success, raises on error/cancellation.
    """
    loop = asyncio.get_running_loop()

    while True:
        # Check for cancellation from the job runtime (user pressed Stop).
        latest = _merge_job_cache_from_db(job_id)
        if latest and latest.get("cancellation_requested"):
            cancel_event.set()
            # Give the subprocess a moment to notice, then hard-kill.
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=3)
            raise SimulationCancelled(SIMULATION_CANCELLED_MESSAGE)

        # Check if the process died unexpectedly (no message on queue).
        if not proc.is_alive():
            # Drain any remaining messages.
            _drain_remaining = _drain_queue_sync(progress_queue)
            for msg in _drain_remaining:
                if msg.get("type") == "result":
                    return msg["data"]
                if msg.get("type") == "error":
                    raise RuntimeError(msg.get("message", "Solver subprocess failed"))
                if msg.get("type") == "cancelled":
                    raise SimulationCancelled(SIMULATION_CANCELLED_MESSAGE)
            raise RuntimeError(
                f"Solver subprocess exited unexpectedly (exit code {proc.exitcode})"
            )

        # Poll the queue in a thread to avoid blocking the event loop.
        try:
            msg = await loop.run_in_executor(
                None, lambda: progress_queue.get(timeout=_SUBPROCESS_QUEUE_POLL_SECONDS)
            )
        except QueueEmpty:
            continue

        msg_type = msg.get("type")
        if msg_type == "result":
            return msg["data"]
        if msg_type == "error":
            raise RuntimeError(msg.get("message", "Solver subprocess failed"))
        if msg_type == "cancelled":
            raise SimulationCancelled(SIMULATION_CANCELLED_MESSAGE)
        if msg_type == "stage":
            _apply_solver_stage_to_job(
                job_id, msg.get("stage", ""), msg.get("progress"), msg.get("message")
            )
        elif msg_type == "progress":
            _apply_solver_stage_to_job(
                job_id, "frequency_solve", msg.get("progress"), None
            )


def _drain_queue_sync(q: "mp.Queue") -> list:
    """Read all immediately available messages from a multiprocessing.Queue."""
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except QueueEmpty:
            break
    return messages


async def run_simulation(job_id: str, request: SimulationRequest) -> None:
    """Run BEM simulation in background."""
    try:
        job = _merge_job_cache_from_db(job_id)
        if not job:
            return
        if job.get("status") == "queued":
            _set_job_fields(
                job_id,
                status="running",
                started_at=_now_iso(),
                stage="initializing",
                stage_message="Initializing BEM solver",
                progress=0.05,
            )
        update_job_stage(
            job_id, "initializing", progress=0.05, stage_message="Initializing BEM solver"
        )
        _raise_if_cancellation_requested(job_id)

        # Initialize solver
        solver = BEMSolver()

        def _cancellation_callback(stage_message: str = CANCELLATION_REQUESTED_MESSAGE) -> None:
            _raise_if_cancellation_requested(job_id, stage_message=stage_message)

        # Extract mesh generation options
        options = request.options if isinstance(request.options, dict) else {}
        mesh_opts = (
            options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
        )
        mesh_strategy = str(mesh_opts.get("strategy", "")).strip().lower()

        if mesh_strategy == "occ_adaptive":
            waveguide_params = mesh_opts.get("waveguide_params")
            if not isinstance(waveguide_params, dict):
                raise ValueError(
                    "options.mesh.waveguide_params must be provided for "
                    "options.mesh.strategy='occ_adaptive'."
                )
            if not WAVEGUIDE_BUILDER_AVAILABLE or build_waveguide_mesh is None or not GMSH_OCC_RUNTIME_READY:
                raise RuntimeError("Adaptive OCC mesh builder is unavailable.")

            update_job_stage(
                job_id, "mesh_prepare", progress=0.15, stage_message="Building adaptive OCC mesh"
            )
            _cancellation_callback("Cancellation requested before adaptive mesh build started")
            validated = WaveguideParamsRequest(**waveguide_params)
            validate_occ_adaptive_bem_shell(validated.enc_depth, validated.wall_thickness)
            validated_payload = validated.model_dump()
            queued_quadrants = int(validated_payload.get("quadrants", 1234))
            # Active OCC solve path always builds full-domain meshes. Non-1234
            # values are tolerated on import for compatibility but are not applied.
            validated_payload["quadrants"] = 1234
            occ_result = build_waveguide_mesh(
                validated_payload,
                include_canonical=True,
                cancellation_callback=lambda: _cancellation_callback(
                    "Cancellation requested while preparing adaptive mesh"
                ),
            )
            _cancellation_callback("Cancellation requested after adaptive mesh build completed")

            # Store mesh artifact for optional download.
            msh_artifact = occ_result.get("msh_text")
            _set_job_fields(
                job_id, mesh_artifact=msh_artifact, has_mesh_artifact=bool(msh_artifact)
            )
            if msh_artifact:
                try:
                    db.store_mesh_artifact(job_id, msh_artifact)
                except Exception as _artifact_persist_exc:
                    # Artifact is optional; do not abort the simulation.
                    logger.warning(
                        "Mesh artifact persistence failed for job %s: %s",
                        job_id,
                        _artifact_persist_exc,
                    )
                    _set_job_fields(job_id, has_mesh_artifact=False)

            # Load mesh for BEM directly from the .msh file via meshio.
            # This bypasses the canonical mesh extraction + normal reorientation
            # pipeline, which can produce wrong normal directions for enclosure
            # meshes.  Loading via meshio matches the approach used in the
            # 260321-BEM reference solver that produces correct directivity.
            if not msh_artifact:
                raise RuntimeError(
                    "Adaptive OCC mesh generation did not produce .msh output."
                )
            from solver.mesh import load_msh_for_bem
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".msh", delete=False, encoding="utf-8"
            ) as tmp_msh:
                tmp_msh.write(msh_artifact)
                tmp_msh_path = tmp_msh.name
            try:
                mesh = load_msh_for_bem(tmp_msh_path, scale_factor=0.001)
            finally:
                try:
                    Path(tmp_msh_path).unlink()
                except OSError:
                    pass

            # Extract canonical mesh stats for the job record (informational only).
            try:
                vertices, indices, surface_tags = _extract_occ_adaptive_canonical_mesh(occ_result)
                canonical_metadata = (
                    occ_result.get("canonical_mesh", {}).get("metadata")
                    if isinstance(occ_result.get("canonical_mesh"), dict)
                    else None
                )
                _set_job_fields(
                    job_id,
                    mesh_stats=_build_mesh_stats(
                        vertices,
                        indices,
                        source="occ_adaptive_msh",
                        surface_tags=surface_tags,
                        metadata=canonical_metadata,
                    ),
                )
            except Exception as _stats_exc:
                logger.warning(
                    "Canonical mesh stats extraction failed for job %s: %s",
                    job_id, _stats_exc,
                )
        else:
            # Legacy canonical path
            update_job_stage(
                job_id, "mesh_prepare", progress=0.15, stage_message="Preparing canonical mesh"
            )
            _cancellation_callback("Cancellation requested before canonical mesh preparation")
            _set_job_fields(
                job_id,
                mesh_stats=_build_mesh_stats(
                    request.mesh.vertices,
                    request.mesh.indices,
                    source="canonical_payload",
                    surface_tags=request.mesh.surfaceTags,
                    metadata=request.mesh.metadata,
                ),
            )
            mesh = solver.prepare_mesh(
                request.mesh.vertices,
                request.mesh.indices,
                surface_tags=request.mesh.surfaceTags,
                boundary_conditions=request.mesh.boundaryConditions,
                mesh_metadata=request.mesh.metadata,
            )

        # Run simulation in a subprocess for hard-kill cancellation support.
        update_job_stage(
            job_id, "bem_solve", progress=0.30, stage_message="Configuring BEM solve"
        )
        _cancellation_callback("Cancellation requested before BEM solve start")

        results = await _run_solve_in_subprocess(job_id, mesh, request)
        _cancellation_callback("Cancellation requested before result persistence")

        # Check for cancellation before storing results.
        latest = _merge_job_cache_from_db(job_id)
        if latest and latest.get("status") == "cancelled":
            _set_job_fields(job_id, stage="cancelled", stage_message="Simulation cancelled")
            return

        # Persist results before marking complete.
        try:
            db.store_results(job_id, results)
        except Exception as persist_exc:
            _set_job_fields(
                job_id,
                status="error",
                stage="error",
                stage_message="Simulation failed",
                error_message="Results could not be saved. The simulation completed but persistence failed.",
                completed_at=_now_iso(),
            )
            logger.error("Persistence error for job %s: %s", job_id, persist_exc)
            return

        completed_at = _now_iso()
        _set_job_fields(
            job_id,
            stage="complete",
            stage_message="Simulation complete",
            progress=1.0,
            status="complete",
            results=results,
            has_results=True,
            completed_at=completed_at,
            cancellation_requested=False,
            error_message=None,
        )

    except SimulationCancelled as exc:
        _finalize_cancelled_job(job_id, stage_message=str(exc) or SIMULATION_CANCELLED_MESSAGE)
        logger.info("Simulation cancellation acknowledged for job %s", job_id)
    except Exception as e:
        # Top-level catch-all: any unhandled exception must transition the job to
        # error state rather than leaving it stuck in 'running'.
        _set_job_fields(
            job_id,
            status="error",
            stage="error",
            stage_message="Simulation failed",
            error_message=str(e),
            completed_at=_now_iso(),
        )
        logger.error("Simulation error for job %s: %s", job_id, e, exc_info=True)
    finally:
        with jobs_lock:
            running_jobs.discard(job_id)
        db.prune_terminal_jobs(retention_days=30, max_terminal_jobs=1000)
        _keep_task(asyncio.create_task(_drain_scheduler_queue()))
