"""Guarded CPU fallback adapter for ``hornlab-bempp-bem``.

The import/load behavior, symmetry, source-motion feature detection, staged
frequency solve, and result mapping port v1
``server/solver/bempp_solver.py:21-67,153-188,229-356``.  Absence is a normal
capability state and never gets faked.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import tempfile
import time
from pathlib import Path
from typing import Any

from server.jobs.models import SolveRequest
from server.mesh.builder import build_solver_mesh

from .base import ArtifactCallback, CancelCallback, EngineRunResult, StageCallback
from .context import SolverContext
from .formulation import DEFAULT_BEM_FORMULATION, DEFAULT_COMPLEX_K_SHIFT
from .infinite_baffle import reject_bempp_infinite_baffle
from .result_mapping import (
    build_solver_response,
    json_safe_native_value,
    native_symmetry_plane,
    observation_config,
    response_solver_log,
)


try:
    from hornlab_bempp_bem import BIEFormulation, ObservationConfig, SolveConfig, solve as bempp_solve
except ImportError:
    BIEFormulation = None  # type: ignore[assignment]
    ObservationConfig = None  # type: ignore[assignment]
    SolveConfig = None  # type: ignore[assignment]
    bempp_solve = None  # type: ignore[assignment]


class BemppUnavailable(RuntimeError):
    """The optional BEMPP fallback package is not importable."""


def _version() -> str | None:
    try:
        return importlib.metadata.version("hornlab-bempp-bem")
    except importlib.metadata.PackageNotFoundError:
        return None


def _load_api() -> bool:
    global BIEFormulation, ObservationConfig, SolveConfig, bempp_solve
    if ObservationConfig is not None and SolveConfig is not None and bempp_solve is not None:
        return True
    try:
        package = importlib.import_module("hornlab_bempp_bem")
        BIEFormulation = getattr(package, "BIEFormulation")
        ObservationConfig = getattr(package, "ObservationConfig")
        SolveConfig = getattr(package, "SolveConfig")
        bempp_solve = getattr(package, "solve")
    except (ImportError, AttributeError):
        return False
    return True


def bempp_status() -> dict[str, Any]:
    """Report real importability; CPU/numba is valid without OpenCL."""

    if not _load_api():
        return {
            "available": False,
            "reason": "hornlab-bempp-bem is not importable (optional CPU fallback not installed).",
            "version": _version(),
            "assembly_backend": None,
        }
    return {
        "available": True,
        "reason": "hornlab-bempp-bem is importable; CPU/numba fallback is available.",
        "version": _version(),
        "assembly_backend": "numba",
    }


def _closed_mode(context: SolverContext) -> bool:
    root = context.design.root
    enclosure = (
        float(root.enclosure.depth.value)
        if root.enclosure is not None and root.enclosure.depth is not None and root.enclosure.depth.value is not None
        else 0.0
    )
    wall = (
        float(root.mesh.wall_thickness.value)
        if root.mesh.wall_thickness is not None and root.mesh.wall_thickness.value is not None
        else 0.0
    )
    return enclosure > 0.0 or wall > 0.0


def solve_bempp_from_msh_text(
    msh_text: str,
    context: SolverContext,
    *,
    mesh_metadata: dict[str, Any] | None = None,
    progress_callback: Any = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
) -> dict[str, Any]:
    """Solve one authoritative Gmsh artifact on the guarded CPU backend."""

    del mesh_metadata
    reject_bempp_infinite_baffle(context)
    if not _load_api() or SolveConfig is None or bempp_solve is None:
        raise BemppUnavailable("hornlab-bempp-bem is not installed.")
    status = bempp_status()
    if not status["available"]:
        raise BemppUnavailable(status["reason"])
    started = time.time()
    if stage_callback:
        stage_callback("setup", 0.0, "Configuring BEMPP BEM solve")

    def progress(index: int, total: int, frequency_hz: float) -> None:
        if cancellation_callback:
            cancellation_callback()
        fraction = index / max(1, total)
        if progress_callback:
            progress_callback(fraction)
        if stage_callback:
            stage_callback(
                "frequency_solve",
                fraction,
                f"Solving frequency {index + 1}/{total} with BEMPP BEM",
            )

    formulation = DEFAULT_BEM_FORMULATION
    if BIEFormulation is not None:
        formulation = getattr(BIEFormulation, "COMPLEX_K", formulation)
    config = SolveConfig(
        freq_min_hz=context.frequency_range[0],
        freq_max_hz=context.frequency_range[1],
        freq_count=context.num_frequencies,
        freq_spacing=context.frequency_spacing,
        formulation=formulation,
        complex_k_shift=DEFAULT_COMPLEX_K_SHIFT,
        observation=observation_config(context, ObservationConfig, BemppUnavailable, "hornlab-bempp-bem"),
        progress_callback=progress,
        mesh_scale=1.0,
        native_symmetry_plane=native_symmetry_plane(context),
        assembly_backend="numba",
        opencl_device="cpu",
        precision="single",
    )
    if context.source_motion != "normal":
        if not hasattr(config, "source_motion"):
            raise BemppUnavailable("Installed hornlab-bempp-bem does not support axial source motion.")
        config.source_motion = context.source_motion
    if hasattr(config, "require_closed_mesh"):
        config.require_closed_mesh = _closed_mode(context)

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".msh", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(msh_text)
            path = Path(handle.name)
        result = bempp_solve(str(path), config)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging BEMPP BEM solver results")

    metadata = {
        "solver_backend": "bempp",
        "solver_mode": "full_3d",
        "engine": "hornlab-bempp-bem",
        "phase_time_convention": "exp(+ikr)",
        "assembly_backend": "numba",
        "assemblyBackend": "numba",
        "device_interface": {"selected": "bempp-cl-numba", "bempp-cl-numba": status},
        "mesh_validation": {"mode": context.mesh_validation_mode, "backend": "hornlab-bempp-bem"},
        "performance": {
            "total_time_seconds": time.time() - started,
            "native_timings": json_safe_native_value(dict(getattr(result, "timings", {}) or {})),
        },
        "bempp": {
            "native_symmetry_plane": getattr(config, "native_symmetry_plane", None),
            "formulation": json_safe_native_value(getattr(config, "formulation", formulation)),
            "complex_k_shift": float(getattr(config, "complex_k_shift", DEFAULT_COMPLEX_K_SHIFT)),
            "assembly_backend": "numba",
            "opencl_device": getattr(config, "opencl_device", "cpu"),
            "precision": getattr(config, "precision", "single"),
            "solver_log": json_safe_native_value(response_solver_log(getattr(result, "solver_log", []))),
        },
    }
    return build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
    )


class BemppEngine:
    name = "bempp"

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
    ) -> EngineRunResult:
        context = SolverContext.from_request(request, solver_mode="full_3d")
        reject_bempp_infinite_baffle(context)
        mesh = await build_solver_mesh(
            request.design,
            request.options,
            cancel_cb,
            lambda stage, progress, message: stage_cb(stage, progress, message),
        )
        if artifact_cb is not None:
            await artifact_cb(mesh["msh_text"], mesh["stats"])
        cancel_cb()
        results = await asyncio.to_thread(
            solve_bempp_from_msh_text,
            mesh["msh_text"],
            context,
            mesh_metadata=mesh["metadata"],
            stage_callback=stage_cb,
            cancellation_callback=cancel_cb,
        )
        results.setdefault("metadata", {})["mesh_stats"] = mesh["stats"]
        return EngineRunResult(results=results, msh_text=mesh["msh_text"], mesh_stats=mesh["stats"])


__all__ = ["BemppEngine", "BemppUnavailable", "bempp_status", "solve_bempp_from_msh_text"]
