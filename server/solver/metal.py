"""Metal full-3D adapter for the pinned ``hornlab-metal-bem`` native helper.

Configuration, native symmetry, coupled infinite-baffle tags, cooperative
frequency cancellation, metadata, and common mapping port v1
``server/solver/metal_solver.py:79-179,182-413``.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import importlib.metadata
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from server.jobs.models import SolveRequest
from server.mesh.builder import _solver_mesher_config, build_solver_mesh

from .base import ArtifactCallback, CancelCallback, EngineRunResult, StageCallback
from .context import SolverContext
from .formulation import DEFAULT_BEM_FORMULATION, DEFAULT_COMPLEX_K_SHIFT
from .infinite_baffle import require_full_3d_aperture_tag
from .result_mapping import (
    build_solver_response,
    json_safe_native_value,
    native_symmetry_plane,
    observation_config,
    response_solver_log,
)


try:
    from hornlab_metal_bem import ObservationConfig, native_config, solve as native_solve
    from hornlab_metal_bem import solve_frequencies as native_solve_frequencies
    from hornlab_metal_bem.backends import discover_metal_backend
    from hornlab_metal_bem.metal.native import discover_native_runtime
except (ImportError, OSError):  # clean capability absence or native loader failure
    ObservationConfig = None  # type: ignore[assignment]
    native_config = None  # type: ignore[assignment]
    native_solve = None  # type: ignore[assignment]
    native_solve_frequencies = None  # type: ignore[assignment]
    discover_metal_backend = None  # type: ignore[assignment]
    discover_native_runtime = None  # type: ignore[assignment]


class MetalUnavailable(RuntimeError):
    """The package or loadable release helper required by Metal is absent."""


logger = logging.getLogger(__name__)


def _version() -> str | None:
    try:
        return importlib.metadata.version("hornlab-metal-bem")
    except importlib.metadata.PackageNotFoundError:
        return None


class _MetalProbeUnavailable(Exception):
    """Carry an unavailable status without letting ``lru_cache`` retain it."""

    def __init__(self, status: dict[str, Any]) -> None:
        super().__init__(status.get("reason", "Metal is unavailable."))
        self.status = status


def _probe_metal_status() -> dict[str, Any]:
    """Probe both package backend and helper executable, as v1 lines 79-139."""

    if discover_metal_backend is None or native_config is None or native_solve is None:
        return {
            "available": False,
            "reason": "hornlab-metal-bem is not importable.",
            "version": _version(),
            "helper_path": None,
        }
    try:
        backend = discover_metal_backend()
    except Exception as exc:
        return {
            "available": False,
            "reason": f"Metal backend probe failed: {exc}",
            "version": _version(),
            "helper_path": None,
        }
    helper_path = None
    helper_loadable = bool(getattr(backend, "native_helper_available", False))
    helper_reason = str(getattr(backend, "reason", "") or "").strip()
    if discover_native_runtime is not None:
        try:
            # Presence alone is not readiness.  The helper smoke opens a Metal
            # device and exercises the executable boundary, making "available"
            # mean present *and loadable* as required by Batch Q.
            runtime = discover_native_runtime(run_smoke_test=True)
            raw_path = getattr(runtime, "helper_executable_path", None)
            helper_path = str(raw_path) if raw_path else None
            helper_loadable = (
                helper_loadable
                and bool(getattr(runtime, "available", False))
                and bool(raw_path)
                and Path(raw_path).is_file()
            )
            if not helper_loadable:
                reasons = list(getattr(runtime, "unavailable_reasons", ()) or ())
                helper_reason = "; ".join(str(reason) for reason in reasons) or str(
                    getattr(runtime, "smoke_test_error", "") or "native helper smoke failed"
                )
        except Exception as exc:
            helper_loadable = False
            helper_reason = f"native helper probe failed: {exc}"
    available = bool(getattr(backend, "available", False)) and helper_loadable
    if available:
        reason = f"Metal native helper detected and loadable at {helper_path}."
    else:
        reason = helper_reason or "Metal native helper is missing or not loadable."
    return {
        "available": available,
        "reason": reason,
        "version": _version(),
        "helper_path": helper_path,
        "supported_platform": bool(getattr(backend, "supported_platform", False)),
        "native_executable": (
            str(getattr(backend, "native_executable"))
            if getattr(backend, "native_executable", None) is not None
            else None
        ),
    }


@lru_cache(maxsize=1)
def _cached_successful_metal_status() -> dict[str, Any]:
    status = _probe_metal_status()
    if not status["available"]:
        # functools does not cache exceptions, so a transient capability
        # failure is retried on the next readiness check.
        raise _MetalProbeUnavailable(status)
    return status


def metal_status() -> dict[str, Any]:
    """Return a defensive snapshot, caching only successful readiness probes."""

    try:
        status = _cached_successful_metal_status()
    except _MetalProbeUnavailable as exc:
        status = exc.status
    return dict(status)


# Preserve the public cache maintenance hook used by tests and app lifecycle
# code without exposing the private cached implementation.
metal_status.cache_clear = _cached_successful_metal_status.cache_clear  # type: ignore[attr-defined]


def _observation(context: SolverContext, msh_text: str) -> Any:
    return observation_config(
        context,
        ObservationConfig,
        MetalUnavailable,
        "hornlab-metal-bem",
        msh_text=msh_text,
    )


def _native_check_open_edges(context: SolverContext) -> bool:
    """Retain v1's reduced bare-shell exception (Metal lines 232-252)."""

    if context.sim_type == 1:
        return True
    if context.mesh_validation_mode != "strict":
        return False
    if native_symmetry_plane(context) is None:
        return True
    root = context.design.root
    enclosure_value = (
        root.enclosure.depth.constant_value()
        if root.enclosure is not None and root.enclosure.depth is not None
        else None
    )
    enclosure_depth = (
        float(enclosure_value)
        if enclosure_value is not None
        else 0.0
    )
    wall_value = (
        root.mesh.wall_thickness.constant_value()
        if root.mesh.wall_thickness is not None
        else None
    )
    wall = (
        float(wall_value)
        if wall_value is not None
        else 0.0
    )
    return enclosure_depth > 0.0 or wall > 0.0


def solve_metal_from_msh_text(
    msh_text: str,
    context: SolverContext,
    *,
    mesh_metadata: dict[str, Any] | None = None,
    progress_callback: Any = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
) -> dict[str, Any]:
    """Run native Metal from an original Gmsh 2.2 text artifact."""

    context.validate()
    if native_config is None or native_solve is None:
        raise MetalUnavailable("hornlab-metal-bem is not installed.")
    status = metal_status()
    if not status["available"]:
        raise MetalUnavailable(status["reason"])
    if context.frequencies_hz is not None and native_solve_frequencies is None:
        raise MetalUnavailable(
            "Installed hornlab-metal-bem does not support explicit frequency lists."
        )
    started = time.time()
    if stage_callback:
        stage_callback("setup", 0.0, "Configuring Metal BEM solve")

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
                f"Solving frequency {index + 1}/{total} with Metal BEM",
            )

    aperture_tag = require_full_3d_aperture_tag(context, mesh_metadata)
    kwargs: dict[str, Any] = {
        "freq_min_hz": context.frequency_range[0],
        "freq_max_hz": context.frequency_range[1],
        "freq_count": context.num_frequencies,
        "freq_spacing": context.frequency_spacing,
        "formulation": DEFAULT_BEM_FORMULATION,
        "complex_k_shift": DEFAULT_COMPLEX_K_SHIFT,
        "observation": _observation(context, msh_text),
        "progress_callback": progress,
        "mesh_scale": 1.0,
        "native_symmetry_plane": native_symmetry_plane(context),
        "native_check_open_edges": _native_check_open_edges(context),
        "mesh_validate": context.mesh_validation_mode != "off",
    }
    if aperture_tag is not None:
        kwargs.update({"aperture_tag": aperture_tag, "mesh_validate": True})
    if context.source_motion != "normal":
        kwargs["source_motion"] = context.source_motion
    try:
        config = native_config(**kwargs)
    except TypeError as exc:
        feature = str(exc)
        if "source_motion" in feature:
            raise MetalUnavailable("Installed hornlab-metal-bem does not support axial source motion.") from exc
        if "aperture_tag" in feature:
            raise MetalUnavailable("Installed hornlab-metal-bem does not support coupled infinite-baffle aperture tags.") from exc
        if "formulation" in feature:
            raise MetalUnavailable("Installed hornlab-metal-bem does not support the required BEM formulation option.") from exc
        if "complex_k_shift" in feature:
            raise MetalUnavailable("Installed hornlab-metal-bem does not support the required complex-k shift option.") from exc
        raise

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".msh", delete=False, encoding="utf-8"
        ) as handle:
            path = Path(handle.name)
            handle.write(msh_text)
        if context.frequencies_hz is None:
            result = native_solve(str(path), config)
        else:
            # solve_frequencies bypasses the generated grid entirely; freq_min/
            # freq_max/freq_count on the config stay as list-derived summaries.
            result = native_solve_frequencies(
                str(path), list(context.frequencies_hz), config
            )
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove temporary Metal mesh %s: %s", path, exc)
    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging Metal BEM solver results")

    metadata: dict[str, Any] = {
        "solver_backend": "metal",
        "solver_mode": "full_3d",
        "device_interface": {"selected": "metal", "metal": status},
        "engine": "hornlab-metal-bem",
        "phase_time_convention": "exp(+ikr)",
        "mesh_validation": {"mode": context.mesh_validation_mode, "backend": "hornlab-metal-bem"},
        "verbose": context.verbose,
        "performance": {
            "total_time_seconds": time.time() - started,
            "native_timings": json_safe_native_value(dict(getattr(result, "timings", {}) or {})),
        },
        "metal": {
            "solver_mode": "full_3d",
            "native_symmetry_plane": getattr(config, "native_symmetry_plane", kwargs["native_symmetry_plane"]),
            "native_check_open_edges": getattr(config, "native_check_open_edges", kwargs["native_check_open_edges"]),
            "formulation": getattr(config, "formulation", kwargs["formulation"]),
            "complex_k_shift": getattr(config, "complex_k_shift", kwargs["complex_k_shift"]),
            "solver_log": json_safe_native_value(response_solver_log(getattr(result, "solver_log", []))),
            "native_diagnostics": json_safe_native_value(list(getattr(result, "native_diagnostics", []) or [])),
        },
    }
    if aperture_tag is not None:
        metadata["metal"]["aperture_tag"] = aperture_tag
        metadata["infinite_baffle"] = {
            "backend": "full_3d_coupled",
            "aperture_tag": aperture_tag,
            "source": "hornlab-waveguide-mesher",
        }
    return build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
    )


class MetalEngine:
    name = "metal"

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
    ) -> EngineRunResult:
        mode = str(request.design.root.simulation.solver_mode or "auto").strip().lower()
        if mode not in {"auto", "full_3d", "circsym"}:
            raise ValueError("solver_mode must be auto, full_3d, or circsym")

        eligibility_reasons: list[str] = []
        if mode != "full_3d":
            from . import circsym as circsym_adapter

            if circsym_adapter.circsym_rejection_reasons is None:
                eligibility_reasons.append(
                    "installed mesher does not expose axisymmetric-meridian eligibility"
                )
            else:
                try:
                    eligibility_reasons.extend(
                        str(reason)
                        for reason in circsym_adapter.circsym_rejection_reasons(
                            _solver_mesher_config(request.design)
                        )
                    )
                except Exception as exc:
                    eligibility_reasons.append(
                        f"axisymmetric-meridian eligibility check failed: {exc}"
                    )
            status = circsym_adapter.circsym_status()
            if not status["available"] and not eligibility_reasons:
                eligibility_reasons.append(str(status["reason"]))

            if mode == "circsym" and eligibility_reasons:
                raise ValueError(
                    "Forced axisymmetric solver mode is not eligible: "
                    + "; ".join(eligibility_reasons)
                )
            if not eligibility_reasons:
                outcome = await circsym_adapter.CircSymEngine().run(
                    request,
                    cancel_cb=cancel_cb,
                    stage_cb=stage_cb,
                    artifact_cb=artifact_cb,
                )
                metadata = outcome.results.setdefault("metadata", {})
                metadata["solve_path"] = "axisymmetric-meridian"
                metadata["axisymmetric_eligibility_reasons"] = []
                metadata["solve_path_reason"] = (
                    "forced by solver_mode='circsym'"
                    if mode == "circsym"
                    else "geometry is eligible for Metal's axisymmetric meridian fast path"
                )
                return outcome

        context = SolverContext.from_request(request, solver_mode="full_3d")
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
            solve_metal_from_msh_text,
            mesh["msh_text"],
            context,
            mesh_metadata=mesh["metadata"],
            stage_callback=stage_cb,
            cancellation_callback=cancel_cb,
        )
        results.setdefault("metadata", {})["mesh_stats"] = mesh["stats"]
        metadata = results.setdefault("metadata", {})
        metadata["solve_path"] = "full-3d"
        metadata["axisymmetric_eligibility_reasons"] = eligibility_reasons
        metadata["solve_path_reason"] = (
            "solver_mode='full_3d' explicitly opts out of the meridian fast path"
            if mode == "full_3d"
            else "axisymmetric-meridian eligibility was rejected"
        )
        return EngineRunResult(results=results, msh_text=mesh["msh_text"], mesh_stats=mesh["stats"])


__all__ = ["MetalEngine", "MetalUnavailable", "metal_status", "solve_metal_from_msh_text"]
