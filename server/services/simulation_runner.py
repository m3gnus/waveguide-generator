"""
BEM simulation runner — executes a single simulation job asynchronously.
"""

import asyncio
import logging
from typing import Any, Optional

from models import SimulationRequest, WaveguideParamsRequest
from solver_bootstrap import (
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


def _validate_occ_adaptive_bem_shell(enc_depth: float, wall_thickness: float) -> None:
    """Adaptive BEM requires either enclosure volume or wall shell thickness."""
    if float(enc_depth) <= 0.0 and float(wall_thickness) <= 0.0:
        raise ValueError(
            "Adaptive BEM simulation requires a closed shell. "
            "Increase enclosure depth or wall thickness."
        )


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

        # Initialize solver
        solver = BEMSolver()

        # Extract mesh generation options
        options = request.options if isinstance(request.options, dict) else {}
        mesh_opts = (
            options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
        )
        mesh_strategy = str(mesh_opts.get("strategy", "")).strip().lower()

        # Check if options are flat or nested
        if "use_gmsh" in options:
            use_gmsh = options.get("use_gmsh", False)
            target_freq = options.get(
                "target_frequency",
                max(request.frequency_range) if request.frequency_range else 1000.0,
            )
        else:
            use_gmsh = mesh_opts.get("use_gmsh", False)
            target_freq = mesh_opts.get(
                "target_frequency",
                max(request.frequency_range) if request.frequency_range else 1000.0,
            )

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
            validated = WaveguideParamsRequest(**waveguide_params)
            _validate_occ_adaptive_bem_shell(validated.enc_depth, validated.wall_thickness)
            validated_payload = validated.model_dump()
            requested_quadrants = int(validated_payload.get("quadrants", 1234))
            # Ensure OCC adaptive simulation mesh is always full domain.
            validated_payload["quadrants"] = 1234
            occ_result = build_waveguide_mesh(validated_payload, include_canonical=True)
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
            # BEM semantics: every non-source surface is rigid wall.
            surface_tags = [2 if int(tag) == 2 else 1 for tag in surface_tags]

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
                    "requestedQuadrants": requested_quadrants,
                    "effectiveQuadrants": 1234,
                    "occStats": occ_result.get("stats") or {},
                }
            )

            mesh = solver.prepare_mesh(
                vertices,
                indices,
                surface_tags=surface_tags,
                boundary_conditions=request.mesh.boundaryConditions,
                mesh_metadata=mesh_metadata,
                use_gmsh=False,
                target_frequency=target_freq,
            )
        else:
            # Legacy canonical path
            update_job_stage(
                job_id, "mesh_prepare", progress=0.15, stage_message="Preparing canonical mesh"
            )
            mesh = solver.prepare_mesh(
                request.mesh.vertices,
                request.mesh.indices,
                surface_tags=request.mesh.surfaceTags,
                boundary_conditions=request.mesh.boundaryConditions,
                mesh_metadata=request.mesh.metadata,
                use_gmsh=use_gmsh,
                target_frequency=target_freq,
            )

        # Run simulation
        update_job_stage(
            job_id, "solver_setup", progress=0.30, stage_message="Configuring BEM solve"
        )

        def _solver_stage_callback(
            stage: str, progress: Optional[float] = None, message: Optional[str] = None
        ) -> None:
            normalized_progress = (
                0.0 if progress is None else max(0.0, min(1.0, float(progress)))
            )

            if stage in {"setup", "solver_setup"}:
                update_job_stage(
                    job_id,
                    "solver_setup",
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
                    "directivity",
                    progress=0.85 + (normalized_progress * 0.13),
                    stage_message=message or (
                        "Generating polar maps (horizontal/vertical/diagonal) "
                        "and deriving DI from solved frequencies"
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
            use_optimized=request.use_optimized,
            enable_symmetry=request.enable_symmetry,
            verbose=request.verbose,
            mesh_validation_mode=request.mesh_validation_mode,
            frequency_spacing=request.frequency_spacing,
            device_mode=request.device_mode,
        )

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
