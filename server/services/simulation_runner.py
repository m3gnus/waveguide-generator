"""
BEM simulation runner — executes a single simulation job asynchronously.
"""

import asyncio
import json
import logging
import math
import tempfile
from pathlib import Path
from typing import Any, Optional

from contracts import SimulationRequest, WaveguideParamsRequest
from services.simulation_validation import (
    is_hornlab_mesher_strategy,
    normalize_waveguide_params_for_solver_backend,
)
from services.gmsh_worker import run_on_gmsh_worker
from services.solver_runtime import (
    HORNLAB_MESHER_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    build_waveguide_mesh,
    resolve_solver_backend,
    solve_bempp_from_msh,
    solve_circsym_from_params,
    solve_metal_from_msh,
)
from solver.axisymmetry import solver_mode_from_request, validate_circsym_axisymmetric
from solver.mesher_adapter import source_motion_from_payload
from services.job_runtime import (
    _merge_job_cache_from_db,
    _set_job_fields,
    _now_iso,
    _keep_task,
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


def _extract_mesher_canonical_mesh(
    mesh_result: dict[str, Any],
) -> tuple[list[Any], list[Any], list[int]]:
    canonical = mesh_result.get("canonical_mesh") or {}
    vertices = canonical.get("vertices")
    indices = canonical.get("indices")
    surface_tags = canonical.get("surfaceTags")
    if (
        not isinstance(vertices, list)
        or not isinstance(indices, list)
        or not isinstance(surface_tags, list)
    ):
        raise RuntimeError(
            "HornLab mesher did not return canonical mesh arrays."
        )
    if len(indices) % 3 != 0:
        raise RuntimeError("HornLab mesher returned invalid triangle index data.")
    if len(surface_tags) != len(indices) // 3:
        raise RuntimeError("HornLab mesher returned mismatched surface tag count.")

    normalized_surface_tags = [int(tag) for tag in surface_tags]
    invalid_tags = sorted({tag for tag in normalized_surface_tags if tag not in CANONICAL_SURFACE_TAGS})
    if invalid_tags:
        raise RuntimeError(
            f"HornLab mesher returned unsupported surface tags: {invalid_tags}."
        )
    if 2 not in normalized_surface_tags:
        raise RuntimeError("HornLab mesher returned no source-tagged elements (tag 2).")
    return vertices, indices, normalized_surface_tags


def _build_mesh_stats(
    vertices: list[Any],
    indices: list[Any],
    *,
    source: str,
    surface_tags: Optional[list[int]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    bounds = _build_vertex_bounds(vertices)
    mesh_stats = {
        "vertex_count": len(vertices) // 3,
        "triangle_count": len(indices) // 3,
        "source": source,
    }
    if bounds is not None:
        min_x, min_y, min_z, max_x, max_y, max_z = bounds
        mesh_stats["bounds_m"] = {
            "min_x": min_x,
            "min_y": min_y,
            "min_z": min_z,
            "max_x": max_x,
            "max_y": max_y,
            "max_z": max_z,
        }
        mesh_stats["dimensions_m"] = {
            "width": max_x - min_x,
            "height": max_z - min_z,
            "depth": max_y - min_y,
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


def _build_vertex_bounds(
    vertices: list[Any],
) -> Optional[tuple[float, float, float, float, float, float]]:
    if len(vertices) < 3:
        return None

    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    found = False
    for index in range(0, len(vertices) - 2, 3):
        try:
            x = float(vertices[index])
            y = float(vertices[index + 1])
            z = float(vertices[index + 2])
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (x, y, z)):
            continue
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        min_z = min(min_z, z)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
        max_z = max(max_z, z)
        found = True

    if not found:
        return None
    return min_x, min_y, min_z, max_x, max_y, max_z


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


async def run_simulation(job_id: str, request: SimulationRequest) -> None:
    """Run BEM simulation in background."""
    tmp_msh_path: str | None = None
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
            job_id, "initializing", progress=0.05, stage_message="Initializing solver"
        )
        _raise_if_cancellation_requested(job_id)
        def _cancellation_callback(stage_message: str = CANCELLATION_REQUESTED_MESSAGE) -> None:
            _raise_if_cancellation_requested(job_id, stage_message=stage_message)

        # Extract mesh generation options
        options = request.options if isinstance(request.options, dict) else {}
        mesh_opts = (
            options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
        )
        mesh_strategy = str(mesh_opts.get("strategy", "")).strip().lower()
        solver_backend = resolve_solver_backend(
            request.solver_backend,
            mesh_strategy=mesh_strategy,
        )
        request.solver_backend = solver_backend

        if not is_hornlab_mesher_strategy(mesh_strategy):
            raise RuntimeError(
                "Simulation requires options.mesh.strategy='hornlab_mesher'. "
                "Client-side JS meshes are viewport-only and are not accepted as a solve pipeline."
            )

        waveguide_params = mesh_opts.get("waveguide_params")
        if not isinstance(waveguide_params, dict):
            raise ValueError(
                "options.mesh.waveguide_params must be provided for "
                "options.mesh.strategy='hornlab_mesher'."
            )
        solver_mode = solver_mode_from_request(request)
        if not HORNLAB_MESHER_AVAILABLE or (
            solver_mode != "circsym"
            and (build_waveguide_mesh is None or not HORNLAB_MESHER_RUNTIME_READY)
        ):
            raise RuntimeError("hornlab-waveguide-mesher is unavailable.")

        update_job_stage(
            job_id,
            "mesh_prepare",
            progress=0.15,
            stage_message=(
                "Preparing CircSym meridian"
                if solver_mode == "circsym"
                else "Building HornLab mesher mesh"
            ),
        )
        _cancellation_callback("Cancellation requested before mesh build started")
        waveguide_params = normalize_waveguide_params_for_solver_backend(
            waveguide_params,
            solver_backend,
        )
        validated = WaveguideParamsRequest(**waveguide_params)
        validated_payload = validated.model_dump()
        # Source velocity BC (1=normal breathing cap, 2=axial rigid piston). Only
        # threaded to the solver when non-default so an older metal-bem stays
        # compatible; the BEMPP fallback rejects axial rather than downgrade it.
        source_motion = source_motion_from_payload(validated_payload)
        solve_source_motion = source_motion if source_motion != "normal" else None
        mesh_stats = None
        if solver_mode == "circsym":
            if solver_backend != "metal":
                raise ValueError(
                    "CircSym requires the Metal backend. The BEMPP backend cannot solve "
                    "axisymmetric CircSym requests; select solver_backend='metal' or use "
                    "solver_mode='full_3d'."
                )
            validate_circsym_axisymmetric(validated_payload)
            _set_job_fields(job_id, mesh_artifact=None, has_mesh_artifact=False)
            update_job_stage(
                job_id,
                "bem_solve",
                progress=0.30,
                stage_message="Configuring CircSym solve",
            )
            _cancellation_callback("Cancellation requested before CircSym solve start")
            results = await asyncio.to_thread(
                solve_circsym_from_params,
                validated_payload,
                request,
                source_motion=solve_source_motion,
                progress_callback=lambda progress: _apply_solver_stage_to_job(
                    job_id, "frequency_solve", progress, None
                ),
                stage_callback=lambda stage, progress, message: _apply_solver_stage_to_job(
                    job_id, stage, progress, message
                ),
            )
            _cancellation_callback("Cancellation requested before result persistence")
        else:
            # Multi-second gmsh build: run it on the dedicated gmsh worker thread
            # so status polling and every other HTTP response stay responsive.
            # asyncio.to_thread is not safe here — gmsh requires all calls on one
            # persistent thread (see services/gmsh_worker.py).
            mesher_result = await run_on_gmsh_worker(
                build_waveguide_mesh,
                validated_payload,
                include_canonical=True,
                cancellation_callback=lambda: _cancellation_callback(
                    "Cancellation requested while preparing solver mesh"
                ),
            )
            _cancellation_callback("Cancellation requested after solver mesh build completed")

            # Store mesh artifact for optional download.
            msh_artifact = mesher_result.get("msh_text")
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
            # This avoids using the viewport/client tessellation as solver input.
            if not msh_artifact:
                raise RuntimeError(
                    "hornlab-waveguide-mesher did not produce .msh output."
                )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".msh", delete=False, encoding="utf-8"
            ) as tmp_msh:
                tmp_msh.write(msh_artifact)
                tmp_msh_path = tmp_msh.name

            # Extract canonical mesh stats for the job record (informational only).
            try:
                vertices, indices, surface_tags = _extract_mesher_canonical_mesh(mesher_result)
                canonical_metadata = (
                    mesher_result.get("canonical_mesh", {}).get("metadata")
                    if isinstance(mesher_result.get("canonical_mesh"), dict)
                    else None
                )
                mesh_stats = _build_mesh_stats(
                    vertices,
                    indices,
                    source="hornlab_waveguide_mesher",
                    surface_tags=surface_tags,
                    metadata=canonical_metadata,
                )
                _set_job_fields(
                    job_id,
                    mesh_stats=mesh_stats,
                )
            except Exception as _stats_exc:
                logger.warning(
                    "Canonical mesh stats extraction failed for job %s: %s",
                    job_id, _stats_exc,
                )

            solver_label = "BEMPP BEM" if solver_backend == "bempp" else "Metal BEM"
            solve_from_msh = solve_bempp_from_msh if solver_backend == "bempp" else solve_metal_from_msh
            update_job_stage(
                job_id,
                "bem_solve",
                progress=0.30,
                stage_message=f"Configuring {solver_label} solve",
            )
            _cancellation_callback("Cancellation requested before solve start")

            if not tmp_msh_path:
                raise RuntimeError(f"{solver_label} solve requires a generated .msh artifact.")
            results = await asyncio.to_thread(
                solve_from_msh,
                tmp_msh_path,
                request,
                source_motion=solve_source_motion,
                progress_callback=lambda progress: _apply_solver_stage_to_job(
                    job_id, "frequency_solve", progress, None
                ),
                stage_callback=lambda stage, progress, message: _apply_solver_stage_to_job(
                    job_id, stage, progress, message
                ),
            )
            _cancellation_callback("Cancellation requested before result persistence")
            if mesh_stats and isinstance(results, dict):
                metadata = results.setdefault("metadata", {})
                if isinstance(metadata, dict):
                    metadata["mesh_stats"] = mesh_stats

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
        if tmp_msh_path:
            try:
                Path(tmp_msh_path).unlink()
            except OSError:
                pass
        with jobs_lock:
            running_jobs.discard(job_id)
        db.prune_terminal_jobs(retention_days=30, max_terminal_jobs=1000)
        _keep_task(asyncio.create_task(_drain_scheduler_queue()))
