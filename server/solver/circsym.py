"""Axisymmetric meridian adapter backed by Metal CircSym.

The mesher-authoritative eligibility check, no-Gmsh meridian path, per-frequency
cancellation, source motion, and coupled-IB aperture mapping port v1
``server/solver/axisymmetry.py:38-124`` and
``server/solver/metal_solver.py:416-566``.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.metadata
import time
from typing import Any

from server.jobs.models import SolveRequest
from server.mesh.builder import _solver_mesher_config

from .acoustics import solver_sound_speed_m_per_s
from .base import (
    ArtifactCallback,
    CancelCallback,
    EngineRunResult,
    ResultCallback,
    StageCallback,
)
from .context import SolverContext
from .frequency_sweep import (
    live_execution_frequencies,
    sort_native_result_frequencies,
)
from .formulation import DEFAULT_BEM_FORMULATION, DEFAULT_COMPLEX_K_SHIFT
from .metal import metal_status
from .result_mapping import (
    build_provisional_frequency_response,
    build_solver_response,
    json_safe_native_value,
    observation_config,
    response_solver_log,
)


try:
    from hornlab_mesher import build_meridian, circsym_rejection_reasons
except (ImportError, OSError):
    build_meridian = None  # type: ignore[assignment]
    circsym_rejection_reasons = None  # type: ignore[assignment]

try:
    from hornlab_metal_bem import MeridianMesh, ObservationConfig, native_config, solve_circsym
    from hornlab_metal_bem import solve_circsym_frequencies
except (ImportError, OSError):
    MeridianMesh = None  # type: ignore[assignment]
    ObservationConfig = None  # type: ignore[assignment]
    native_config = None  # type: ignore[assignment]
    solve_circsym = None  # type: ignore[assignment]
    solve_circsym_frequencies = None  # type: ignore[assignment]


class CircSymUnavailable(RuntimeError):
    """The mesher meridian capability or Metal CircSym runtime is absent."""


_CIRCSYM_ELEMENTS_PER_WAVELENGTH = 6.0
_CIRCSYM_DEFAULT_RESOLUTION_MM = {
    "throatResolution": 3.0,
    "mouthResolution": 12.0,
    "rearResolution": 18.0,
}


def _frequency_refined_meridian_config(
    config: dict[str, Any],
    max_frequency_hz: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Clamp private CircSym segment targets to wavelength/6 at the sweep top."""

    frequency = float(max_frequency_hz)
    if not frequency > 0.0:
        raise ValueError("CircSym maximum frequency must be positive")
    sound_speed = solver_sound_speed_m_per_s("hornlab_metal_bem")
    max_segment_mm = (
        1000.0
        * float(sound_speed)
        / (frequency * _CIRCSYM_ELEMENTS_PER_WAVELENGTH)
    )
    refined = deepcopy(config)
    mesh = refined.setdefault("mesh", {})
    requested: dict[str, float] = {}
    applied: dict[str, float] = {}
    for key, default in _CIRCSYM_DEFAULT_RESOLUTION_MM.items():
        value = float(mesh.get(key, default))
        requested[key] = value
        applied[key] = min(value, max_segment_mm)
        mesh[key] = applied[key]

    aperture_scale = float(mesh.get("apertureResolutionScale", 1.0))
    requested["apertureResolutionScale"] = aperture_scale
    max_aperture_scale = max(1.0, max_segment_mm / applied["mouthResolution"])
    applied["apertureResolutionScale"] = min(aperture_scale, max_aperture_scale)
    mesh["apertureResolutionScale"] = applied["apertureResolutionScale"]
    return refined, {
        "policy": "wavelength_over_6_max_segment",
        "max_frequency_hz": frequency,
        "sound_speed_m_per_s": float(sound_speed),
        "max_segment_mm": max_segment_mm,
        "requested": requested,
        "applied": applied,
        "refined": any(applied[key] < requested[key] for key in requested),
    }


def circsym_observation_rejection_reason(context: SolverContext) -> str | None:
    """Return why the requested observation grid needs a full-3D mesh, if any."""

    if ObservationConfig is None:
        return None
    try:
        observation_config(
            context,
            ObservationConfig,
            CircSymUnavailable,
            "hornlab-metal-bem",
        )
    except CircSymUnavailable:
        return "a non-45-degree diagonal plane requires the full-3D mesh"
    return None


def _validated_aperture_tag(metadata: Any, sim_type: int) -> int | None:
    raw = (metadata if isinstance(metadata, dict) else {}).get("apertureTag")
    if sim_type == 1 and raw is None:
        raise ValueError("infinite-baffle CircSym solve requires a positive aperture tag")
    if raw is None:
        return None
    try:
        tag = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("CircSym aperture tag must be a positive integer") from exc
    if tag <= 0:
        raise ValueError("CircSym aperture tag must be a positive integer")
    return tag


def circsym_status() -> dict[str, Any]:
    mesher_ready = build_meridian is not None and circsym_rejection_reasons is not None
    metal = metal_status()
    native_ready = all(value is not None for value in (MeridianMesh, ObservationConfig, native_config, solve_circsym))
    available = mesher_ready and native_ready and bool(metal["available"])
    if not mesher_ready:
        reason = "hornlab-waveguide-mesher does not expose build_meridian/CircSym capability."
    elif not native_ready:
        reason = "hornlab-metal-bem does not expose the CircSym native API."
    elif not metal["available"]:
        reason = f"CircSym mesher capability detected, but Metal is unavailable: {metal['reason']}"
    else:
        reason = "CircSym meridian capability and loadable Metal helper detected."
    try:
        version = importlib.metadata.version("hornlab-waveguide-mesher")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {"available": available, "reason": reason, "version": version}


def solve_circsym_design(
    context: SolverContext,
    *,
    progress_callback: Any = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None,
) -> dict[str, Any]:
    """Build a meridian and run the adaptively Metal-accelerated sweep."""

    context.validate()
    if any(value is None for value in (build_meridian, MeridianMesh, ObservationConfig, native_config, solve_circsym)):
        raise CircSymUnavailable("Installed mesher/Metal packages do not provide CircSym.")
    status = circsym_status()
    if not status["available"]:
        raise CircSymUnavailable(status["reason"])
    if context.frequencies_hz is not None and solve_circsym_frequencies is None:
        raise CircSymUnavailable(
            "Installed hornlab-metal-bem does not support explicit CircSym frequency lists."
        )
    observation_rejection = circsym_observation_rejection_reason(context)
    if observation_rejection is not None:
        raise CircSymUnavailable(observation_rejection)
    if cancellation_callback:
        cancellation_callback()
    started = time.time()
    execution_frequencies = live_execution_frequencies(context)
    config_dict, frequency_refinement = _frequency_refined_meridian_config(
        _solver_mesher_config(context.design),
        float(max(execution_frequencies)),
    )
    reasons = list(circsym_rejection_reasons(config_dict))
    if reasons:
        raise ValueError("CircSym requires a circular waveguide: " + "; ".join(str(reason) for reason in reasons))
    if stage_callback:
        stage_callback("mesh_prepare", 1.0, "Building axisymmetric meridian")
    meridian_build = build_meridian(config_dict)
    meridian = meridian_build.as_metal_meridian(MeridianMesh)
    if cancellation_callback:
        cancellation_callback()
    last_inner_cancel_check = time.monotonic()

    def should_continue() -> bool:
        """Poll durable cancellation inside a frequency without hammering SQLite."""
        nonlocal last_inner_cancel_check
        now = time.monotonic()
        if (
            cancellation_callback is not None
            and now - last_inner_cancel_check >= 0.05
        ):
            last_inner_cancel_check = now
            cancellation_callback()
        return True

    def progress(index: int, total: int, frequency_hz: float) -> None:
        fraction = index / max(1, total)
        if progress_callback:
            progress_callback(fraction)
        if stage_callback:
            stage_callback(
                "frequency_solve",
                fraction,
                f"Solving frequency {index + 1}/{total} with Axisymmetric Metal",
            )

    def on_frequency_result(index: int, frequency_hz: float, entry: dict[str, Any]) -> bool:
        if cancellation_callback:
            cancellation_callback()
        if result_callback is not None:
            result_callback(
                index,
                build_provisional_frequency_response(
                    index=index,
                    frequency_hz=frequency_hz,
                    entry=entry,
                    config=config,
                    context=context,
                    backend="metal",
                    sound_speed_m_per_s=solver_sound_speed_m_per_s(
                        "hornlab_metal_bem"
                    ),
                ),
            )
        return True

    if stage_callback:
        stage_callback("setup", 0.0, "Configuring Axisymmetric Metal solve")
    kwargs: dict[str, Any] = {
        "freq_min_hz": context.frequency_range[0],
        "freq_max_hz": context.frequency_range[1],
        "freq_count": context.num_frequencies,
        "freq_spacing": context.frequency_spacing,
        "formulation": DEFAULT_BEM_FORMULATION,
        "complex_k_shift": DEFAULT_COMPLEX_K_SHIFT,
        "observation": observation_config(context, ObservationConfig, CircSymUnavailable, "hornlab-metal-bem"),
        "progress_callback": progress,
        "circsym_baffle_z": meridian_build.baffle_z,
    }
    aperture_tag = _validated_aperture_tag(
        getattr(meridian_build, "metadata", None), context.sim_type
    )
    if aperture_tag is not None:
        kwargs["circsym_aperture_tag"] = aperture_tag
    if cancellation_callback is not None or result_callback is not None:
        kwargs["on_frequency_result"] = on_frequency_result
    if cancellation_callback is not None:
        kwargs["should_continue"] = should_continue
    if context.source_motion != "normal":
        kwargs["source_motion"] = context.source_motion
    try:
        config = native_config(**kwargs)
    except TypeError as exc:
        message = str(exc)
        if "circsym_aperture_tag" in message:
            raise CircSymUnavailable("Installed Metal helper lacks coupled infinite-baffle CircSym support.") from exc
        if "source_motion" in message:
            raise CircSymUnavailable("Installed Metal helper lacks axial CircSym source motion.") from exc
        if "on_frequency_result" in message:
            raise CircSymUnavailable("Installed Metal helper lacks cancellable CircSym sweeps.") from exc
        if "should_continue" in message:
            raise CircSymUnavailable(
                "Installed Metal helper lacks intra-frequency CircSym cancellation."
            ) from exc
        if "circsym_baffle_z" in message:
            raise CircSymUnavailable("Installed Metal helper lacks the required CircSym baffle position option.") from exc
        if "formulation" in message:
            raise CircSymUnavailable("Installed Metal helper lacks the required BEM formulation option.") from exc
        if "complex_k_shift" in message:
            raise CircSymUnavailable("Installed Metal helper lacks the required complex-k shift option.") from exc
        raise
    if result_callback is not None and solve_circsym_frequencies is not None:
        result = solve_circsym_frequencies(
            meridian, live_execution_frequencies(context).tolist(), config
        )
        sort_native_result_frequencies(result)
    elif context.frequencies_hz is None:
        result = solve_circsym(meridian, config)
    else:
        result = solve_circsym_frequencies(
            meridian, list(context.frequencies_hz), config
        )
    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging CircSym solver results")

    metal = metal_status()
    native_diagnostics = list(getattr(result, "native_diagnostics", []) or [])
    compute_backends = {
        "assembly": sorted(
            {
                str(entry.get("assembly_backend"))
                for entry in native_diagnostics
                if isinstance(entry, dict) and entry.get("assembly_backend")
            }
        ),
        "field": sorted(
            {
                str(entry.get("field_backend"))
                for entry in native_diagnostics
                if isinstance(entry, dict) and entry.get("field_backend")
            }
        ),
    }
    metadata: dict[str, Any] = {
        "solver_backend": "metal",
        "solver_mode": "circsym",
        "solve_path": "axisymmetric-meridian",
        "axisymmetric_eligibility_reasons": [],
        "device_interface": {
            "selected": "metal",
            "metal": metal,
            "circsym_compute_backends": compute_backends,
        },
        "engine": "hornlab-metal-bem",
        "phase_time_convention": "exp(+ikr)",
        "mesh_validation": {"mode": context.mesh_validation_mode, "backend": "hornlab-metal-bem-circsym"},
        "verbose": context.verbose,
        "performance": {
            "total_time_seconds": time.time() - started,
            "native_timings": json_safe_native_value(dict(getattr(result, "timings", {}) or {})),
        },
        "metal": {
            "solver_mode": "circsym",
            "circsym_baffle_z": getattr(config, "circsym_baffle_z", meridian_build.baffle_z),
            "formulation": getattr(config, "formulation", kwargs["formulation"]),
            "complex_k_shift": getattr(config, "complex_k_shift", kwargs["complex_k_shift"]),
            "solver_log": json_safe_native_value(response_solver_log(getattr(result, "solver_log", []))),
            "native_diagnostics": json_safe_native_value(native_diagnostics),
            "compute_backends": compute_backends,
            "meridian": json_safe_native_value(dict(getattr(meridian_build, "metadata", {}) or {})),
            "meridian_frequency_refinement": frequency_refinement,
        },
    }
    if aperture_tag is not None:
        metadata["metal"]["aperture_tag"] = int(aperture_tag)
        if context.sim_type == 1:
            metadata["infinite_baffle"] = {
                "backend": "circsym_coupled",
                "aperture_tag": int(aperture_tag),
                "source": "hornlab-waveguide-mesher",
            }
    return build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
        sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_metal_bem"),
    )


class CircSymEngine:
    name = "circsym"

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
        result_cb: ResultCallback | None = None,
    ) -> EngineRunResult:
        del artifact_cb
        context = SolverContext.from_request(request, solver_mode="circsym")
        results = await asyncio.to_thread(
            solve_circsym_design,
            context,
            stage_callback=stage_cb,
            cancellation_callback=cancel_cb,
            result_callback=result_cb,
        )
        return EngineRunResult(results=results)


__all__ = [
    "CircSymEngine",
    "CircSymUnavailable",
    "circsym_observation_rejection_reason",
    "circsym_status",
    "solve_circsym_design",
]
