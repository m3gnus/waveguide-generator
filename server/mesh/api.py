"""Viewport access to the authoritative solver-mesh artifact.

``POST /api/solver-mesh`` builds (or re-serves from the shared artifact cache)
the exact Gmsh 2.2 artifact a parametric solve would run on, so the viewport
can draw real solver triangles instead of the smooth preview tessellation.
The build queues FIFO on the persistent single-thread gmsh worker, which is
the same serialization a solve submission observes; symmetry is resolved with
the same helpers the solve pipeline uses, so the reduced domain and its cut
planes match what the solver will actually assemble.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable, Literal

from fastapi import FastAPI, HTTPException, Request

from pydantic import BaseModel

from server.design.schema import DesignConfig, Expr
from server.solver.symmetry import resolve_symmetry, validate_symmetry_mode

#: How often the disconnect watcher samples the request socket. Coarse on
#: purpose: the cooperative cancel checkpoints inside the builder are sparse,
#: so a finer poll would not cancel any sooner.
DISCONNECT_POLL_SECONDS = 0.25

#: The positive-side domain retained for each ATH quadrant mask, expressed as
#: the origin cut planes the frontend mirrors across (``symmetryScene.ts``).
#: Matches ``server.mesh.builder._symmetry_plane_axes`` (axis 0 = x, 1 = y).
CUT_PLANES_BY_QUADRANTS: dict[int, tuple[str, ...]] = {
    1: ("x0", "y0"),
    12: ("y0",),
    14: ("x0",),
    1234: (),
}

SymmetryMode = Literal["auto", "full", "half_xz", "half_yz", "quarter"]


class SolverMeshRequest(BaseModel):
    """The design exactly as a solve submission would carry it."""

    design: DesignConfig
    symmetry: SymmetryMode = "auto"


class ClientDisconnected(Exception):
    """The requesting viewport went away mid-build."""


async def solver_mesh_response(
    design: DesignConfig,
    symmetry: SymmetryMode,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """Resolve symmetry, build the artifact, and shape the viewport payload.

    Separated from the route so tests exercise the full behaviour without
    fabricating a ``Request``. ``is_disconnected`` is polled on a side task;
    the builder's cooperative cancel checkpoints observe the resulting flag
    from the gmsh worker thread and abandon the build via
    ``ClientDisconnected``.
    """

    # Deferred like the other builder consumers: the mesher import graph is
    # heavy and a process that never builds must never pay for it.
    from server.mesh.builder import build_solver_mesh

    resolution = await asyncio.to_thread(resolve_symmetry, design)
    try:
        quadrants = validate_symmetry_mode(symmetry, resolution)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Stamp the resolved domain exactly as runtime._execution_request does, so
    # the artifact -- and decisively its cache entry -- is the one the solve
    # will reuse rather than a full-domain sibling.
    stamped = design.model_copy(deep=True)
    stamped.root.mesh.quadrants = Expr(value=float(quadrants))

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
            stamped,
            {"mesh_validation_mode": "warn"},
            cancel_cb=_cancel_cb,
        )
    except ClientDisconnected as exc:
        # Nobody is listening; 499 mirrors the conventional client-closed
        # status and keeps the abandoned build out of the error logs.
        raise HTTPException(status_code=499, detail=str(exc)) from exc
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
    }


def mount_solver_mesh(application: FastAPI) -> None:
    @application.post("/api/solver-mesh")
    async def solver_mesh(request: Request, body: SolverMeshRequest) -> dict[str, Any]:
        return await solver_mesh_response(
            body.design,
            body.symmetry,
            request.is_disconnected,
        )


__all__ = [
    "CUT_PLANES_BY_QUADRANTS",
    "ClientDisconnected",
    "SolverMeshRequest",
    "mount_solver_mesh",
    "solver_mesh_response",
]
