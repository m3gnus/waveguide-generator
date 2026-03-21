"""
BEM simulation runner — executes a single simulation job asynchronously.
"""

import asyncio
import json
import logging
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
    update_job_stage,
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
            occ_result = build_waveguide_mesh(
                validated_payload,
                include_canonical=True,
                cancellation_callback=lambda: _cancellation_callback(
                    "Cancellation requested while preparing adaptive mesh"
                ),
            )
            _cancellation_callback("Cancellation requested after adaptive mesh build completed")
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
                    source="occ_adaptive_canonical",
                    surface_tags=surface_tags,
                    metadata=canonical_metadata,
                ),
            )

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

            mesh_metadata = dict(request.mesh.metadata or {})
            mesh_metadata.update(
                {
                    "units": "mm",
                    "unitScaleToMeter": 0.001,
                    "meshStrategy": "occ_adaptive",
                    "generatedBy": "gmsh-occ",
                    "requestedQuadrants": queued_quadrants,
                    "effectiveQuadrants": queued_quadrants,
                    "occStats": occ_result.get("stats") or {},
                    "verticalOffset": float(validated_payload.get("vertical_offset", 0) or 0),
                }
            )

            mesh = solver.prepare_mesh(
                vertices,
                indices,
                surface_tags=surface_tags,
                boundary_conditions=request.mesh.boundaryConditions,
                mesh_metadata=mesh_metadata,
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

        # Run simulation
        update_job_stage(
            job_id, "bem_solve", progress=0.30, stage_message="Configuring BEM solve"
        )
        _cancellation_callback("Cancellation requested before BEM solve start")

        def _solver_stage_callback(
            stage: str, progress: Optional[float] = None, message: Optional[str] = None
        ) -> None:
            normalized_progress = (
                0.0 if progress is None else max(0.0, min(1.0, float(progress)))
            )

            if stage in {"setup", "solver_setup"}:
                update_job_stage(
                    job_id,
                    "bem_solve",
                    progress=0.30 + (normalized_progress * 0.05),
                    stage_message=message or "Configuring BEM solve",
                )
                return

            if stage == "frequency_solve":
                update_job_stage(
                    job_id,
                    "bem_solve",
                    progress=0.35 + (normalized_progress * 0.50),
                    stage_message=message or "Solving BEM frequencies",
                )
                return

            if stage == "directivity":
                update_job_stage(
                    job_id,
                    "finalizing",
                    progress=0.85 + (normalized_progress * 0.13),
                    stage_message=message or (
                        "Generating requested polar maps and deriving DI from solved frequencies"
                    ),
                )
                return

            if stage == "finalizing":
                update_job_stage(
                    job_id,
                    "finalizing",
                    progress=0.98 + (normalized_progress * 0.01),
                    stage_message=message or "Finalizing results",
                )
                return

            update_job_stage(job_id, str(stage), stage_message=message)

        results = await asyncio.to_thread(
            solver.solve,
            mesh=mesh,
            frequency_range=request.frequency_range,
            num_frequencies=request.num_frequencies,
            sim_type=request.sim_type,
            polar_config=(
                request.polar_config.model_dump() if request.polar_config else None
            ),
            progress_callback=lambda p: _solver_stage_callback("frequency_solve", progress=p),
            stage_callback=_solver_stage_callback,
            # Legacy field kept in request contract for compatibility only.
            use_optimized=request.use_optimized,
            verbose=request.verbose,
            mesh_validation_mode=request.mesh_validation_mode,
            frequency_spacing=request.frequency_spacing,
            device_mode=request.device_mode,
            advanced_settings=(
                request.advanced_settings.model_dump(exclude_none=True)
                if request.advanced_settings
                else None
            ),
            cancellation_callback=lambda: _cancellation_callback(
                "Cancellation requested during BEM solve"
            ),
        )
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
        asyncio.create_task(_drain_scheduler_queue())
