"""Viewport access to the authoritative solver-mesh artifact.

``POST /api/solver-mesh`` builds (or re-serves from the shared artifact cache)
the exact Gmsh 2.2 artifact a parametric solve would run on, so the viewport
can draw real solver triangles instead of the smooth preview tessellation.
The build queues FIFO on the persistent single-thread gmsh worker, which is
the same serialization a solve submission observes. Submission resolution and
execution transforms are shared with the solve pipeline.
"""

from __future__ import annotations

import logging
from server.preview.errors import user_error_detail

from typing import Any, Awaitable, Callable, Literal

from fastapi import FastAPI, HTTPException, Request

from pydantic import BaseModel, Field

from server.contracts.geometry import CUT_PLANES_BY_QUADRANTS as CUT_PLANES_BY_QUADRANTS
from server.design.schema import DesignConfig
from server.engines.registry import EngineRegistry
from server.jobs.request_cancellation import ClientDisconnected, RequestCancellation
from server.jobs.models import SolveOptions, SolveRequest
from server.jobs.plans import SolvePlanResponse, solve_plan_response
from server.jobs.runtime.core import SubmissionResolution, _execution_request, resolve_submission
from server.mesh.child import MesherShuttingDownError
from server.mesh.errors import MesherEstimateUnavailable, MeshTooLarge, SolverMemoryTooLarge

logger = logging.getLogger(__name__)

SymmetryMode = Literal["auto", "full", "half_xz", "half_yz", "quarter"]


class SolverMeshRequest(BaseModel):
    """The design exactly as a solve submission would carry it."""

    design: DesignConfig
    symmetry: SymmetryMode = "auto"
    options: SolveOptions = Field(default_factory=SolveOptions)


class SurfaceDeviationStats(BaseModel):
    peak_is_upper_bound: bool = False
    peak_mm: float | None = None
    p95_mm: float | None = None
    rms_mm: float | None = None
    triangle_count: int = 0
    sampled_triangle_count: int = 0


class ElementShapeStats(BaseModel):
    p1_radius_ratio: float | None = None
    p1_angle_deg: float | None = None
    min_angle_deg: float | None = None
    sliver_fraction: float | None = None


class SolverMeshStats(BaseModel):
    triangle_count: int
    vertex_count: int
    warnings: list[str]
    mesh_cache_key: str
    mesh_cache_hit: bool
    surface_deviation_mm: float | None = None
    surface_deviation: SurfaceDeviationStats | None = None
    element_shape: ElementShapeStats | None = None


class MeshEstimateRequest(BaseModel):
    design: DesignConfig


class MeshEstimateResponse(BaseModel):
    triangle_count: int
    surface_deviation_mm: float | None


class SolverMeshResponse(BaseModel):
    msh_text: str
    stats: SolverMeshStats
    cut_planes: list[str]
    quadrants: int
    plan: SolvePlanResponse


async def _resolve_mesh_execution(
    design: DesignConfig, options: SolveOptions, engine_registry: EngineRegistry,
) -> tuple[SubmissionResolution, SolveRequest]:
    """Use the same effective mesh inputs for an estimate and its solver view."""
    resolved = await resolve_submission(
        SolveRequest.model_validate({"design": design, "options": options}),
        engine_registry,
        preview=True,
    )
    quadrants = int(resolved.symmetry_metadata["resolved_quadrants"])
    return resolved, _execution_request(resolved.request, resolved_quadrants=quadrants)


async def solver_mesh_response(
    design: DesignConfig,
    symmetry: SymmetryMode | None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    *,
    options: SolveOptions | None = None,
    engine_registry: EngineRegistry,
) -> dict[str, Any]:
    """Resolve the solve request, build its artifact, and return mesh plus plan.

    Separated from the route so tests exercise the full behaviour without
    fabricating a ``Request``. ``is_disconnected`` is polled on a side task;
    the builder's cooperative cancel checkpoints observe the resulting flag
    from the gmsh worker thread and abandon the build via
    ``ClientDisconnected``.
    """

    # Deferred like the other builder consumers: the mesher import graph is
    # heavy and a process that never builds must never pay for it.
    from hornlab_mesher.mesher import MesherError
    from server.mesh.builder import build_solver_mesh

    options = (options or SolveOptions()).model_copy(deep=True)
    if symmetry is not None:
        options.symmetry = symmetry
    try:
        resolved, execution = await _resolve_mesh_execution(design, options, engine_registry)
        quadrants = int(resolved.symmetry_metadata["resolved_quadrants"])
    except (ValueError, MesherError) as exc:
        raise HTTPException(status_code=422, detail=user_error_detail(exc)) from exc

    cancellation = RequestCancellation(is_disconnected)

    try:
        result = await build_solver_mesh(
            execution.design,
            execution.options,
            cancel_cb=cancellation.check,
        )
    except ClientDisconnected as exc:
        # Nobody is listening; 499 mirrors the conventional client-closed
        # status and keeps the abandoned build out of the error logs.
        raise HTTPException(status_code=499, detail="Mesh preparation was cancelled.") from exc
    except MesherShuttingDownError as exc:
        raise HTTPException(status_code=503, detail="WG is shutting down. Open WG again to prepare this mesh.") from exc
    except MeshTooLarge as exc:
        raise HTTPException(status_code=409, detail={
            "code": "mesh_too_large", "message": str(exc),
            "triangles": exc.count, "limit": exc.limit, "mesh_cache_key": exc.mesh_cache_key,
        }) from exc
    except SolverMemoryTooLarge as exc:
        logger.warning("Solver memory admission refused a mesh: %s", exc)
        raise HTTPException(status_code=422, detail=SolverMemoryTooLarge.USER_MESSAGE) from exc
    except MesherEstimateUnavailable as exc:
        logger.exception("Cannot check mesh memory before generation")
        raise HTTPException(status_code=422, detail=MesherEstimateUnavailable.USER_MESSAGE) from exc
    except (RuntimeError, ValueError, MesherError) as exc:
        raise HTTPException(status_code=422, detail=user_error_detail(exc)) from exc
    finally:
        cancellation.close()

    stats = result["stats"]
    return {
        "msh_text": result["msh_text"],
        "stats": {
            "triangle_count": int(stats["triangle_count"]),
            "vertex_count": int(stats["vertex_count"]),
            "warnings": [str(warning) for warning in stats.get("warnings", [])],
            "mesh_cache_key": str(stats["mesh_cache_key"]),
            "mesh_cache_hit": bool(stats["mesh_cache_hit"]),
            "surface_deviation_mm": result.get("metadata", {}).get("surface_deviation_mm"),
            "surface_deviation": result.get("metadata", {}).get("surface_deviation"),
            "element_shape": result.get("metadata", {}).get("quality", {}).get("element_shape"),
        },
        "cut_planes": list(CUT_PLANES_BY_QUADRANTS[quadrants]),
        "quadrants": quadrants,
        "plan": solve_plan_response(resolved).model_dump(mode="json"),
    }


def mount_solver_mesh(application: FastAPI, engine_registry: EngineRegistry) -> None:
    @application.post("/api/mesh-estimate", response_model=MeshEstimateResponse)
    async def mesh_estimate(request: Request, body: MeshEstimateRequest) -> dict[str, Any]:
        from server.mesh.builder import estimate_solver_mesh
        from hornlab_mesher.mesher import MesherError

        cancellation = RequestCancellation(request.is_disconnected)
        try:
            design = body.design
            if design.root.assembly is None:
                _resolved, execution = await _resolve_mesh_execution(design, SolveOptions(), engine_registry)
                design = execution.design
            return await estimate_solver_mesh(design, cancel_cb=cancellation.check)
        except ClientDisconnected as exc:
            raise HTTPException(status_code=499, detail="Mesh preparation was cancelled.") from exc
        except SolverMemoryTooLarge as exc:
            logger.warning("Solver memory admission refused a mesh estimate: %s", exc)
            raise HTTPException(status_code=422, detail=SolverMemoryTooLarge.USER_MESSAGE) from exc
        except MesherEstimateUnavailable as exc:
            logger.exception("Cannot check mesh memory before generation")
            raise HTTPException(status_code=422, detail=MesherEstimateUnavailable.USER_MESSAGE) from exc
        except (RuntimeError, ValueError, MesherError) as exc:
            raise HTTPException(status_code=422, detail=user_error_detail(exc)) from exc
        finally:
            cancellation.close()

    @application.post("/api/solver-mesh", response_model=SolverMeshResponse)
    async def solver_mesh(request: Request, body: SolverMeshRequest) -> dict[str, Any]:
        return await solver_mesh_response(
            body.design,
            body.symmetry if "symmetry" in body.model_fields_set else None,
            request.is_disconnected,
            options=body.options,
            engine_registry=engine_registry,
        )


__all__ = [
    "CUT_PLANES_BY_QUADRANTS",
    "ClientDisconnected",
    "SolverMeshRequest",
    "mount_solver_mesh",
    "solver_mesh_response",
]
