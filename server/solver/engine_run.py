"""The one run template behind the Metal, BEAT and BEMPP engine ports.

Each engine's ``run`` used to spell out the same two skeletons: build or read
the mesh, hand it to the artifact callback, check cancellation, solve, stamp
the mesh stats, then unpack the artifacts the solve left in its result dict.
The engines now supply only the solve itself (and any metadata of their own);
this module owns the sequence and the hand-off from a solve's results to the
typed ``EngineRunResult``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from server.mesh.builder import build_solver_mesh
from server.jobs.models import SolveRequest

from .base import (
    ArtifactCallback,
    CancelCallback,
    EngineRunResult,
    StageCallback,
)
from .imported import read_verified_import_mesh, verify_record_mesh_text

MetadataHook = Callable[[dict[str, Any]], None]


def full_3d_solve_path(metadata: dict[str, Any]) -> None:
    """The BEAT and BEMPP design solves' only metadata of their own."""

    metadata["solve_path"] = "full-3d"


async def run_parametric_engine(
    request: SolveRequest,
    *,
    cancel_cb: CancelCallback,
    stage_cb: StageCallback,
    artifact_cb: ArtifactCallback | None,
    solve: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]],
    annotate: MetadataHook | None = None,
) -> EngineRunResult:
    """Mesh the design, store the mesh, solve it, and package the outcome.

    ``solve`` receives the built mesh after the artifact is stored and
    cancellation is checked. ``annotate`` adds engine metadata after the mesh
    stats are stamped.
    """

    mesh = await build_solver_mesh(
        request.design,
        request.options,
        cancel_cb,
        lambda stage, progress, message: stage_cb(stage, progress, message),
    )
    # The stored artifact is always the design's own mesh, whatever the solve
    # does with it (a laddered sweep says so in its metadata).
    if artifact_cb is not None:
        await artifact_cb(mesh["msh_text"], mesh["stats"])
    cancel_cb()
    results = await solve(mesh)
    metadata = results.setdefault("metadata", {})
    metadata["mesh_stats"] = mesh["stats"]
    if annotate is not None:
        annotate(metadata)
    return EngineRunResult.from_solve_results(
        results, msh_text=mesh["msh_text"], mesh_stats=mesh["stats"],
    )


async def run_imported_engine(
    record: Mapping[str, Any],
    *,
    cancel_cb: CancelCallback,
    artifact_cb: ArtifactCallback | None,
    solve: Callable[[str], Awaitable[dict[str, Any]]],
    annotate: MetadataHook | None = None,
) -> EngineRunResult:
    """Solve the verified record mesh, never a re-mesh.

    Every engine is handed the same bytes for the same record: the runtime's
    already-verified execution text when it has one, else the record's file.
    """

    execution_msh = record.get("_execution_msh_text")
    if isinstance(execution_msh, str):
        msh_text = verify_record_mesh_text(record, execution_msh)
    else:
        msh_text = await asyncio.to_thread(read_verified_import_mesh, record)
    mesh_record = record.get("mesh")
    mesh_stats = (
        dict(mesh_record.get("stats") or {}) if isinstance(mesh_record, Mapping) else {}
    )
    if artifact_cb is not None:
        await artifact_cb(msh_text, mesh_stats)
    cancel_cb()
    results = await solve(msh_text)
    metadata = results.setdefault("metadata", {})
    metadata["mesh_stats"] = mesh_stats
    if annotate is not None:
        annotate(metadata)
    return EngineRunResult.from_solve_results(
        results, msh_text=msh_text, mesh_stats=mesh_stats,
    )


__all__ = [
    "full_3d_solve_path",
    "run_imported_engine",
    "run_parametric_engine",
]
