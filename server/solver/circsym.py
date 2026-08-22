"""Platform-neutral axisymmetric meridian solver adapter.

The mesher-authoritative eligibility check, no-Gmsh meridian path, per-frequency
cancellation, source motion, and coupled-IB aperture mapping port v1
``server/solver/axisymmetry.py:38-124`` and
``server/solver/metal_solver.py:416-566``.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.metadata
import math
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
    """The mesher meridian capability or portable CircSym API is absent."""


def metal_status() -> dict[str, Any]:
    """Optional acceleration status; never controls axisymmetric availability."""
    from .metal import metal_status as probe

    return probe()


_CIRCSYM_ELEMENTS_PER_WAVELENGTH = 6.0
_CIRCSYM_AZIMUTH_POINTS_MIN = 64
_CIRCSYM_AZIMUTH_POINTS_PER_KRHO = 4.0
_CIRCSYM_LINE_QUADRATURE_ORDER = 4
_COMPLEX128_BYTES = 16
_CIRCSYM_DEFAULT_RESOLUTION_MM = {
    "throatResolution": 6.0,
    "mouthResolution": 15.0,
    "rearResolution": 40.0,
}
_CIRCSYM_DEFAULT_APERTURE_RESOLUTION_SCALE = 1.5


def _positive_resolution(value: Any, key: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"axisymmetric mesh control {key} must be a fixed numeric value; "
            "formula-valued mesh resolutions require full 3D"
        ) from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(
            f"axisymmetric mesh control {key} must be finite and positive"
        )
    return number


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
        value = _positive_resolution(mesh.get(key, default), key)
        requested[key] = value
        applied[key] = min(value, max_segment_mm)
        mesh[key] = applied[key]

    aperture_scale = _positive_resolution(
        mesh.get(
            "apertureResolutionScale",
            _CIRCSYM_DEFAULT_APERTURE_RESOLUTION_SCALE,
        ),
        "apertureResolutionScale",
    )
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
    native_ready = all(value is not None for value in (MeridianMesh, ObservationConfig, native_config, solve_circsym))
    available = mesher_ready and native_ready
    if not mesher_ready:
        reason = "hornlab-waveguide-mesher does not expose build_meridian/CircSym capability."
    elif not native_ready:
        reason = "hornlab-metal-bem does not expose the portable CircSym API."
    else:
        reason = (
            "Portable axisymmetric meridian solver detected; CPU execution is "
            "available on this platform and Metal acceleration is optional."
        )
    try:
        version = importlib.metadata.version("hornlab-waveguide-mesher")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {"available": available, "reason": reason, "version": version}


def axisymmetric_eligibility_reasons(request: SolveRequest) -> list[str]:
    """Return authoritative geometry/runtime reasons the meridian path cannot run."""
    if circsym_rejection_reasons is None:
        return [
            "hornlab-waveguide-mesher does not expose the axisymmetric "
            "eligibility predicate"
        ]
    mesher_config: dict[str, Any] | None = None
    try:
        mesher_config = _solver_mesher_config(request.design)
        reasons = [
            str(reason)
            for reason in circsym_rejection_reasons(
                mesher_config
            )
        ]
    except Exception as exc:
        return [f"axisymmetric geometry eligibility probe failed: {exc}"]
    try:
        context = SolverContext.from_request(request, solver_mode="circsym")
        observation_reason = circsym_observation_rejection_reason(context)
        if mesher_config is not None:
            _frequency_refined_meridian_config(
                mesher_config,
                float(max(live_execution_frequencies(context))),
            )
    except Exception as exc:
        reasons.append(f"axisymmetric runtime eligibility probe failed: {exc}")
    else:
        if observation_reason is not None:
            reasons.append(observation_reason)
    status = circsym_status()
    if not status["available"] and not reasons:
        reasons.append(str(status["reason"]))
    return reasons


def axisymmetric_plan_cost(
    request: SolveRequest,
    *,
    full_3d_quadrants: int = 1234,
) -> dict[str, Any]:
    """Return transparent work/memory evidence for the formulation planner.

    This is deliberately a complexity model, not a wall-clock promise. Native
    Metal, BLAS, CPU generation, and thermal state make elapsed-time constants
    host-specific. The counts below are deterministic properties of the exact
    frequency-refined meridian and requested observations, so AUTO can explain
    why it selected the reduced formulation without pretending one developer
    machine is every user's hardware.
    """

    if build_meridian is None:
        raise CircSymUnavailable("The installed mesher cannot build a meridian.")
    context = SolverContext.from_request(request, solver_mode="circsym")
    frequencies = [float(value) for value in live_execution_frequencies(context)]
    refined, refinement = _frequency_refined_meridian_config(
        _solver_mesher_config(request.design),
        max(frequencies),
    )
    build = build_meridian(refined)
    segment_count = int(build.segments.shape[0])
    if segment_count <= 0:
        raise ValueError("axisymmetric cost model requires a non-empty meridian")

    rho_max = max(float(node[0]) for node in build.nodes)
    sound_speed = solver_sound_speed_m_per_s("hornlab_metal_bem")
    complex_k_scale = math.hypot(1.0, float(DEFAULT_COMPLEX_K_SHIFT))
    azimuth_orders = [
        max(
            _CIRCSYM_AZIMUTH_POINTS_MIN,
            int(
                math.ceil(
                    _CIRCSYM_AZIMUTH_POINTS_PER_KRHO
                    * 2.0
                    * math.pi
                    * frequency
                    * complex_k_scale
                    * rho_max
                    / sound_speed
                )
            ),
        )
        for frequency in frequencies
    ]

    polar_targets = int(context.polar_config["angle_range"][2])
    sphere_targets = (
        int(context.polar_config.get("spherical_theta_count", 37))
        if context.polar_config.get("spherical_sampling")
        else 0
    )
    # Every enabled plane and every azimuth at a given polar angle collapse to
    # the same (rho,z) target for m=0. This mirrors the native exact-dedupe path.
    unique_observation_targets = polar_targets + sphere_targets
    azimuth_sum = sum(azimuth_orders)
    assembly_terms = (
        _CIRCSYM_LINE_QUADRATURE_ORDER
        * segment_count
        * segment_count
        * azimuth_sum
    )
    field_terms = (
        _CIRCSYM_LINE_QUADRATURE_ORDER
        * segment_count
        * unique_observation_targets
        * azimuth_sum
    )

    # Estimate a corresponding triangle-of-revolution mesh at the meridian's
    # actual local segment length. It is not a substitute for Gmsh's realized
    # count, but it is a reproducible full-3D dense-matrix scale comparator.
    full_revolution_triangles = 0
    for raw_segment in build.segments:
        start_index, end_index = (int(value) for value in raw_segment)
        start = build.nodes[start_index]
        end = build.nodes[end_index]
        length = math.hypot(
            float(end[0]) - float(start[0]),
            float(end[1]) - float(start[1]),
        )
        rho_mid = 0.5 * (float(start[0]) + float(end[0]))
        if length <= 0.0 or rho_mid <= 0.0:
            continue
        azimuth_segments = max(8, int(math.ceil(2.0 * math.pi * rho_mid / length)))
        touches_axis = min(float(start[0]), float(end[0])) <= 1.0e-12
        full_revolution_triangles += azimuth_segments * (1 if touches_axis else 2)

    requested_quadrants = {
        int(char) for char in str(int(full_3d_quadrants)) if char in "1234"
    }
    quadrant_fraction = max(1, len(requested_quadrants)) / 4.0
    full_3d_triangles = max(
        segment_count,
        int(math.ceil(full_revolution_triangles * quadrant_fraction)),
    )
    full_3d_dense_entries = full_3d_triangles * full_3d_triangles * len(frequencies)
    axisym_matrix_bytes = segment_count * segment_count * _COMPLEX128_BYTES
    full_3d_matrix_bytes = full_3d_triangles * full_3d_triangles * _COMPLEX128_BYTES

    return {
        "model": "deterministic-reduced-vs-revolved-dense-v1",
        "frequency_count": len(frequencies),
        "frequency_max_hz": max(frequencies),
        "meridian_segments": segment_count,
        "rho_max_m": rho_max,
        "azimuth_quadrature": {
            "minimum": min(azimuth_orders),
            "maximum": max(azimuth_orders),
            "sum": azimuth_sum,
        },
        "unique_observation_targets_per_frequency": unique_observation_targets,
        "axisymmetric": {
            "matrix_bytes": axisym_matrix_bytes,
            "ring_quadrature_terms": assembly_terms + field_terms,
            "assembly_terms": assembly_terms,
            "field_terms": field_terms,
        },
        "full_3d_equivalent": {
            "requested_quadrants": int(full_3d_quadrants),
            "domain_fraction": quadrant_fraction,
            "estimated_triangles": full_3d_triangles,
            "matrix_bytes": full_3d_matrix_bytes,
            "dense_entries_across_sweep": full_3d_dense_entries,
        },
        "relative_dense_unknowns": full_3d_triangles / segment_count,
        "relative_dense_matrix_memory": full_3d_matrix_bytes / axisym_matrix_bytes,
        "meridian_frequency_refinement": refinement,
        "selection": (
            "axisymmetric preserves the requested m=0 physics while reducing "
            "the dense boundary system; native timing remains host-dependent"
        ),
    }


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
                f"Solving frequency {index + 1}/{total} with Axisymmetric meridian BEM",
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
                    backend="axisym",
                    sound_speed_m_per_s=solver_sound_speed_m_per_s(
                        "hornlab_metal_bem"
                    ),
                ),
            )
        return True

    if stage_callback:
        stage_callback("setup", 0.0, "Configuring axisymmetric meridian solve")
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
            raise CircSymUnavailable("Installed axisymmetric solver lacks coupled infinite-baffle support.") from exc
        if "source_motion" in message:
            raise CircSymUnavailable("Installed axisymmetric solver lacks axial source motion.") from exc
        if "on_frequency_result" in message:
            raise CircSymUnavailable("Installed axisymmetric solver lacks cancellable sweeps.") from exc
        if "should_continue" in message:
            raise CircSymUnavailable(
                "Installed axisymmetric solver lacks intra-frequency cancellation."
            ) from exc
        if "circsym_baffle_z" in message:
            raise CircSymUnavailable("Installed axisymmetric solver lacks the required baffle position option.") from exc
        if "formulation" in message:
            raise CircSymUnavailable("Installed axisymmetric solver lacks the required BEM formulation option.") from exc
        if "complex_k_shift" in message:
            raise CircSymUnavailable("Installed axisymmetric solver lacks the required complex-k shift option.") from exc
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
    accelerated_by_metal = any(
        isinstance(entry, dict)
        and (
            entry.get("assembly_backend") == "metal"
            or "metal" in str(entry.get("field_backend", "")).lower()
        )
        for entry in native_diagnostics
    )
    selected_device = "metal" if accelerated_by_metal else "cpu"
    acceleration_status = metal_status()
    metadata: dict[str, Any] = {
        "solver_backend": "axisym",
        "solver_mode": "circsym",
        "solve_path": "axisymmetric-meridian",
        "axisymmetric_eligibility_reasons": [],
        "device_interface": {
            "selected": selected_device,
            "metal_acceleration": acceleration_status,
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
        "axisym": {
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
        metadata["axisym"]["aperture_tag"] = int(aperture_tag)
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


class AxisymmetricEngine:
    name = "axisym"

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
        metadata = results.setdefault("metadata", {})
        metadata["solve_path_reason"] = (
            "forced by solver_mode='circsym'"
            if request.options.solver_mode == "circsym"
            else "AUTO selected the eligible platform-neutral axisymmetric runner"
        )
        return EngineRunResult(
            results=results,
            field_trace_unavailable_reason="unsupported_axisymmetric_formulation",
        )


# Compatibility alias for older internal tests and extensions. The registered
# engine name is ``axisym``; ``circsym`` remains only the legacy mode value.
CircSymEngine = AxisymmetricEngine


__all__ = [
    "CircSymEngine",
    "AxisymmetricEngine",
    "CircSymUnavailable",
    "circsym_observation_rejection_reason",
    "circsym_status",
    "axisymmetric_eligibility_reasons",
    "axisymmetric_plan_cost",
    "solve_circsym_design",
]
