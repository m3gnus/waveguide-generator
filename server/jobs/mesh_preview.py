"""Viewport access to the authoritative solver-mesh artifact.

``POST /api/solver-mesh`` builds (or re-serves from the shared artifact cache)
the exact Gmsh 2.2 artifact a parametric solve would run on, so the viewport
can draw real solver triangles instead of the smooth preview tessellation.
The build queues FIFO on the persistent single-thread gmsh worker, which is
the same serialization a solve submission observes. Submission resolution and
execution transforms are shared with the solve pipeline.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable, Literal

from fastapi import FastAPI, HTTPException, Request

from pydantic import BaseModel, Field

from server.contracts.geometry import CUT_PLANES_BY_QUADRANTS as CUT_PLANES_BY_QUADRANTS
from server.design.schema import DesignConfig
from server.engines.registry import EngineRegistry
from server.jobs.models import SolveOptions, SolveRequest
from server.jobs.plans import SolvePlanResponse, solve_plan_response
from server.jobs.runtime.core import _execution_request, resolve_submission
from server.mesh.child import MesherShuttingDownError

#: How often the disconnect watcher samples the request socket. Coarse on
#: purpose: the cooperative cancel checkpoints inside the builder are sparse,
#: so a finer poll would not cancel any sooner.
DISCONNECT_POLL_SECONDS = 0.25

SymmetryMode = Literal["auto", "full", "half_xz", "half_yz", "quarter"]


class SolverMeshRequest(BaseModel):
    """The design exactly as a solve submission would carry it."""

    design: DesignConfig
    symmetry: SymmetryMode = "auto"
    options: SolveOptions = Field(default_factory=SolveOptions)


class SolverMeshStats(BaseModel):
    triangle_count: int
    vertex_count: int
    warnings: list[str]
    mesh_cache_key: str
    mesh_cache_hit: bool


class SolverMeshResponse(BaseModel):
    msh_text: str
    stats: SolverMeshStats
    cut_planes: list[str]
    quadrants: int
    plan: SolvePlanResponse


class ClientDisconnected(Exception):
    """The requesting viewport went away mid-build."""


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
    from server.mesh.builder import build_solver_mesh

    options = (options or SolveOptions()).model_copy(deep=True)
    if symmetry is not None:
        options.symmetry = symmetry
    try:
        resolved = await resolve_submission(
            SolveRequest.model_validate({"design": design, "options": options}),
            engine_registry,
            preview=True,
        )
        quadrants = int(resolved.symmetry_metadata["resolved_quadrants"])
        execution = _execution_request(resolved.request, resolved_quadrants=quadrants)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    disconnected = threading.Event()
    watcher: asyncio.Task[None] | None = None
    if is_disconnected is not None:
        watch = is_disconnected

        async def _watch() -> None:
            while not await watch():
                await asyncio.sleep(DISCONNECT_POLL_SECONDS)
            disconnected.set()

        watcher = asyncio.create_task(_watch())

    def _cancel_cb() -> None:
        if disconnected.is_set():
            raise ClientDisconnected("solver-mesh client disconnected")

    try:
        result = await build_solver_mesh(
            execution.design,
            execution.options,
            cancel_cb=_cancel_cb,
        )
    except ClientDisconnected as exc:
        # Nobody is listening; 499 mirrors the conventional client-closed
        # status and keeps the abandoned build out of the error logs.
        raise HTTPException(status_code=499, detail=str(exc)) from exc
    except MesherShuttingDownError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if watcher is not None:
            watcher.cancel()

    stats = result["stats"]
    return {
        "msh_text": result["msh_text"],
        "stats": {
            "triangle_count": int(stats["triangle_count"]),
            "vertex_count": int(stats["vertex_count"]),
            "warnings": [str(warning) for warning in stats.get("warnings", [])],
            "mesh_cache_key": str(stats["mesh_cache_key"]),
            "mesh_cache_hit": bool(stats["mesh_cache_hit"]),
        },
        "cut_planes": list(CUT_PLANES_BY_QUADRANTS[quadrants]),
        "quadrants": quadrants,
        "plan": solve_plan_response(resolved).model_dump(mode="json"),
    }


def mount_solver_mesh(application: FastAPI, engine_registry: EngineRegistry) -> None:
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
