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
from typing import Any, Mapping

import numpy as np

from server.jobs.models import ImportedGeometrySource, SolveRequest
from server.mesh.builder import _solver_mesher_config, build_solver_mesh

from .acoustics import solver_sound_speed_m_per_s
from .combine import combine_drive_channels, serialize_channel_bases
from .driver_lem import channel_drive_scaling
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
from .infinite_baffle import require_full_3d_aperture_tag
from .imported import (
    global_frequency_caveat,
    imported_symmetry_from_cut_planes,
    mesh_frequency_validation,
    read_verified_import_mesh,
    verify_record_mesh_text,
)
from .result_mapping import (
    build_provisional_frequency_response,
    build_solver_response,
    json_safe_native_value,
    native_symmetry_plane,
    native_observation_frame,
    observation_config,
    response_solver_log,
)


try:
    from hornlab_metal_bem import (
        AxialProfile,
        NormalProfile,
        ObservationConfig,
        ObservationFrame,
        native_config,
        solve as native_solve,
        solve_multi_source as native_solve_multi_source,
    )
    from hornlab_metal_bem import solve_frequencies as native_solve_frequencies
    from hornlab_metal_bem.backends import discover_metal_backend
    from hornlab_metal_bem.metal.native import discover_native_runtime
except (ImportError, OSError):  # clean capability absence or native loader failure
    ObservationConfig = None  # type: ignore[assignment]
    AxialProfile = None  # type: ignore[assignment]
    NormalProfile = None  # type: ignore[assignment]
    ObservationFrame = None  # type: ignore[assignment]
    native_config = None  # type: ignore[assignment]
    native_solve = None  # type: ignore[assignment]
    native_solve_multi_source = None  # type: ignore[assignment]
    native_solve_frequencies = None  # type: ignore[assignment]
    discover_metal_backend = None  # type: ignore[assignment]
    discover_native_runtime = None  # type: ignore[assignment]


class MetalUnavailable(RuntimeError):
    """The package or loadable release helper required by Metal is absent."""


logger = logging.getLogger(__name__)


def _native_config_or_unavailable(kwargs: Mapping[str, Any]) -> Any:
    """Apply one capability-error ladder to parametric and imported configs."""

    assert native_config is not None
    try:
        return native_config(**dict(kwargs))
    except TypeError as exc:
        feature = str(exc)
        if "source_velocity_profiles" in feature:
            raise MetalUnavailable(
                "Installed hornlab-metal-bem does not support mixed per-channel source motion."
            ) from exc
        if "source_motion" in feature:
            raise MetalUnavailable(
                "Installed hornlab-metal-bem does not support axial source motion."
            ) from exc
        if "aperture_tag" in feature:
            raise MetalUnavailable(
                "Installed hornlab-metal-bem does not support coupled infinite-baffle aperture tags."
            ) from exc
        if "formulation" in feature:
            raise MetalUnavailable(
                "Installed hornlab-metal-bem does not support the required BEM formulation option."
            ) from exc
        if "complex_k_shift" in feature:
            raise MetalUnavailable(
                "Installed hornlab-metal-bem does not support the required complex-k shift option."
            ) from exc
        if "frame_override" in feature:
            raise MetalUnavailable(
                "Installed hornlab-metal-bem does not support the required explicit observation frame."
            ) from exc
        raise


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


def _observation(
    context: SolverContext,
    msh_text: str,
    *,
    aperture_tag: int | None = None,
) -> Any:
    return observation_config(
        context,
        ObservationConfig,
        MetalUnavailable,
        "hornlab-metal-bem",
        msh_text=msh_text,
        aperture_tag=aperture_tag,
    )


def _native_check_open_edges(context: SolverContext) -> bool:
    """Retain v1's reduced bare-shell exception (Metal lines 232-252)."""

    if context.sim_type == 1:
        return True
    if context.mesh_validation_mode != "strict":
        return False
    if native_symmetry_plane(context) is None:
        return True
    if context.design is None:
        return False
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
    result_callback: ResultCallback | None = None,
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

    def on_frequency_result(
        index: int, frequency_hz: float, entry: dict[str, Any]
    ) -> bool:
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

    aperture_tag = require_full_3d_aperture_tag(context, mesh_metadata)
    frame_override = native_observation_frame(
        context,
        msh_text,
        ObservationFrame,
        aperture_tag=aperture_tag,
    )
    kwargs: dict[str, Any] = {
        "freq_min_hz": context.frequency_range[0],
        "freq_max_hz": context.frequency_range[1],
        "freq_count": context.num_frequencies,
        "freq_spacing": context.frequency_spacing,
        "formulation": DEFAULT_BEM_FORMULATION,
        "complex_k_shift": DEFAULT_COMPLEX_K_SHIFT,
        "observation": _observation(
            context,
            msh_text,
            aperture_tag=aperture_tag,
        ),
        "progress_callback": progress,
        "mesh_scale": 1.0,
        "native_symmetry_plane": native_symmetry_plane(context),
        "native_check_open_edges": _native_check_open_edges(context),
        "mesh_validate": context.mesh_validation_mode != "off",
    }
    if result_callback is not None:
        kwargs["on_frequency_result"] = on_frequency_result
    if frame_override is not None:
        kwargs["frame_override"] = frame_override
    if aperture_tag is not None:
        kwargs.update({"aperture_tag": aperture_tag, "mesh_validate": True})
    if context.source_motion != "normal":
        kwargs["source_motion"] = context.source_motion
    config = _native_config_or_unavailable(kwargs)

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".msh", delete=False, encoding="utf-8"
        ) as handle:
            path = Path(handle.name)
            handle.write(msh_text)
        if result_callback is not None and native_solve_frequencies is not None:
            result = native_solve_frequencies(
                str(path), live_execution_frequencies(context).tolist(), config
            )
            sort_native_result_frequencies(result)
        elif context.frequencies_hz is None:
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
        sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_metal_bem"),
    )


def _imported_frame(record: Mapping[str, Any], context: SolverContext) -> Any:
    if ObservationFrame is None:
        raise MetalUnavailable(
            "Installed hornlab-metal-bem does not expose ObservationFrame."
        )
    anchor = record.get("anchor")
    anchor = anchor if isinstance(anchor, Mapping) else {}
    frame = anchor.get("throat_frame")
    if not isinstance(frame, Mapping):
        normalisation = record.get("normalisation")
        normalisation = normalisation if isinstance(normalisation, Mapping) else {}
        frame = normalisation.get("anchor_throat_frame")
    if not isinstance(frame, Mapping):
        normalisation = record.get("normalisation")
        normalisation = normalisation if isinstance(normalisation, Mapping) else {}
        if bool(normalisation.get("assembly_frame_is_solver_frame")):
            frame = {
                "axis": [0.0, 0.0, 1.0],
                "origin_m": [0.0, 0.0, 0.0],
                "u": [1.0, 0.0, 0.0],
                "v": [0.0, 1.0, 0.0],
                "mouth_center_m": [0.0, 0.0, 0.0],
                "source_center_m": [0.0, 0.0, 0.0],
            }
        else:
            raise ValueError(
                "ingestion record has no anchor throat frame; re-ingest the CAD return"
            )

    def vector(name: str, *fallback_names: str) -> np.ndarray:
        value = frame.get(name)
        if value is None:
            for fallback in fallback_names:
                value = frame.get(fallback)
                if value is not None:
                    break
        result = np.asarray(value, dtype=float)
        if result.shape != (3,) or not np.isfinite(result).all():
            raise ValueError(f"ingestion anchor throat frame {name!r} must be a finite 3-vector")
        return result

    axis = vector("axis", "normal")
    axis /= np.linalg.norm(axis)
    source_center = vector("source_center_m", "origin_m", "origin")
    mouth_center = vector("mouth_center_m", "origin_m", "origin")
    origin = source_center
    return ObservationFrame(
        axis=axis,
        origin=origin,
        u=vector("u", "horizontal"),
        v=vector("v", "vertical"),
        mouth_center=mouth_center,
        source_center=source_center,
    )


def _imported_validity_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    validation = mesh_frequency_validation(record)
    per_source_raw = validation.get("per_source")
    per_source_raw = per_source_raw if isinstance(per_source_raw, Mapping) else {}
    per_source: dict[str, Any] = {}
    for source_id, item in per_source_raw.items():
        if not isinstance(item, Mapping):
            continue
        per_source[str(source_id)] = {
            key: json_safe_native_value(value)
            for key, value in item.items()
            if key not in {"tag", "name"}
        }
    return per_source


def _record_source_area_m2(record: Mapping[str, Any], source_id: str) -> float:
    """The source's full physical area from the ingestion record, in m²."""

    for source in record.get("sources") or []:
        if not isinstance(source, Mapping) or str(source.get("id")) != source_id:
            continue
        observed = source.get("observed")
        observed = observed if isinstance(observed, Mapping) else {}
        area_mm2 = observed.get("total_area_mm2")
        if isinstance(area_mm2, (int, float)) and float(area_mm2) > 0.0:
            return float(area_mm2) * 1.0e-6
    raise ValueError(
        f"driver coupling needs a recorded positive area for source {source_id!r}"
    )


def _apply_channel_driver(
    channel: Any,
    result: Any,
    record: Mapping[str, Any],
    source_tags: Mapping[str, Any],
    *,
    drive_voltage_v: float,
    rg_ohm: float,
) -> dict[str, Any]:
    """Scale one channel's raw fields to the voltage-driven driver output."""

    source_id = channel.source_ids[0]
    area_m2 = _record_source_area_m2(record, source_id)
    tag = int(source_tags[source_id])
    surface_avg = getattr(result, "surface_pressure_avg", None)
    p_avg = surface_avg.get(tag) if isinstance(surface_avg, Mapping) else None
    if p_avg is None:
        # Per-channel results from the multi-RHS solve report the driven
        # tag's area-weighted average surface pressure as ``impedance``.
        p_avg = result.impedance
    scale_raw, payload = channel_drive_scaling(
        np.asarray(result.frequencies_hz, dtype=np.float64).reshape(-1),
        np.asarray(p_avg, dtype=np.complex128),
        area_m2,
        channel.driver,
        drive_voltage_v=drive_voltage_v,
        rg_ohm=rg_ohm,
    )
    result.pressure_complex = (
        np.asarray(result.pressure_complex, dtype=np.complex128)
        * scale_raw[:, None, None]
    )
    sphere = getattr(result, "sphere_pressure_complex", None)
    if sphere is not None:
        result.sphere_pressure_complex = (
            np.asarray(sphere, dtype=np.complex128) * scale_raw[:, None]
        )
    payload["source_id"] = source_id
    payload["source_area_m2"] = area_m2
    return payload


def _combined_channel_response(
    *,
    geometry: ImportedGeometrySource,
    sorted_results: Mapping[str, Any],
    request: SolveRequest,
    quadrants: int,
    config: Any,
    config_motion: str,
    started: float,
    status: Mapping[str, Any],
    kwargs: Mapping[str, Any],
    per_source_validity: Mapping[str, Any],
) -> dict[str, Any]:
    """Package the LR4 time-aligned sum as one more contract-shaped channel."""

    spec = geometry.combine
    assert spec is not None
    channels_by_id = {channel.id: channel for channel in geometry.drive_channels}
    member_validity_hz: dict[str, float] = {}
    for member in spec.members:
        limits = [
            float(item["effective_max_valid_frequency_hz"])
            for source_id in channels_by_id[member].source_ids
            if isinstance(item := per_source_validity.get(source_id), Mapping)
            and item.get("effective_max_valid_frequency_hz") is not None
        ]
        if limits:
            member_validity_hz[member] = min(limits)

    combined_result, combine_payload = combine_drive_channels(
        sorted_results,
        members=list(spec.members),
        crossovers_hz=list(spec.crossovers_hz),
        level_match=spec.level_match,
        align=spec.align,
        member_validity_hz=member_validity_hz,
    )
    source_ids = [
        source_id
        for member in spec.members
        for source_id in channels_by_id[member].source_ids
    ]
    metadata = {
        "solver_backend": "metal",
        "solver_mode": "full_3d",
        "geometry_type": "imported",
        "drive_channel_id": spec.id,
        "derived_from_channels": list(spec.members),
        "source_ids": source_ids,
        "device_interface": {"selected": "metal", "metal": dict(status)},
        "engine": "hornlab-metal-bem",
        "phase_time_convention": "exp(+ikr)",
        "combine": combine_payload,
        "mesh_validation": {
            "mode": request.options.mesh_validation_mode,
            "backend": "hornlab-metal-bem",
        },
        "performance": {"total_time_seconds": time.time() - started},
        "metal": {
            "solver_mode": "full_3d",
            "native_symmetry_plane": kwargs["native_symmetry_plane"],
            "native_check_open_edges": False,
            "formulation": kwargs["formulation"],
            "complex_k_shift": kwargs["complex_k_shift"],
            "solver_log": json_safe_native_value(
                response_solver_log(getattr(combined_result, "solver_log", []))
            ),
        },
    }
    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion
    )
    response = build_solver_response(
        result=combined_result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
        sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_metal_bem"),
    )
    response.pop("impedance", None)
    response["metadata"]["impedance_omitted"] = (
        "combined channel: member drives differ; no single impedance exists"
    )
    return response


def solve_imported_metal_from_msh_text(
    msh_text: str,
    request: SolveRequest,
    record: Mapping[str, Any],
    *,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None,
) -> dict[str, Any]:
    """Solve every imported drive channel as a shared multi-RHS unit basis."""

    geometry = request.geometry
    if not isinstance(geometry, ImportedGeometrySource):
        raise ValueError("imported Metal solve requires imported geometry")
    if native_config is None or native_solve_multi_source is None:
        raise MetalUnavailable(
            "Installed hornlab-metal-bem does not support multi-source solves."
        )
    status = metal_status()
    if not status["available"]:
        raise MetalUnavailable(status["reason"])

    symmetry = record.get("symmetry")
    symmetry = symmetry if isinstance(symmetry, Mapping) else {}
    cut_planes = {str(value) for value in symmetry.get("cut_planes") or []}
    imported_symmetry = imported_symmetry_from_cut_planes(cut_planes)
    quadrants = imported_symmetry.quadrants
    source_tags = record.get("source_tags")
    if not isinstance(source_tags, Mapping):
        raise ValueError("ingestion record has no source tag map")
    channel_order = [channel.id for channel in geometry.drive_channels]
    channels: dict[str, Any] = {}
    started = time.time()
    if stage_callback:
        stage_callback("setup", 0.0, "Configuring imported multi-source Metal BEM solve")

    motions = {channel.motion for channel in geometry.drive_channels}
    config_motion = next(iter(motions)) if len(motions) == 1 else "normal"
    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion
    )
    context.validate()

    source_specs: list[dict[int, complex]] = []
    source_profiles: dict[int, Any] = {}
    for channel in geometry.drive_channels:
        spec: dict[int, complex] = {}
        for source_id in channel.source_ids:
            if source_id not in source_tags:
                raise ValueError(
                    f"ingestion tag map has no active source {source_id!r}"
                )
            tag = int(source_tags[source_id])
            spec[tag] = 1.0 + 0.0j
            if len(motions) > 1:
                profile_cls = AxialProfile if channel.motion == "axial" else NormalProfile
                if profile_cls is None:
                    raise MetalUnavailable(
                        "Installed hornlab-metal-bem does not support mixed per-channel source motion."
                    )
                source_profiles[tag] = profile_cls()
        source_specs.append(spec)

    def progress(index: int, total: int, frequency_hz: float) -> None:
        if cancellation_callback:
            cancellation_callback()
        fraction = index / max(1, total)
        if stage_callback:
            stage_callback(
                "frequency_solve",
                fraction,
                f"Solving frequency {index + 1}/{total} for imported drive bases",
            )

    def on_frequency_result(
        index: int, frequency_hz: float, entry: dict[str, Any]
    ) -> bool:
        if cancellation_callback:
            cancellation_callback()
        if result_callback is None:
            return True
        source_entries = entry.get("source_results")
        if not isinstance(source_entries, list) or len(source_entries) != len(
            geometry.drive_channels
        ):
            raise ValueError(
                "streamed imported Metal result does not match its drive channels"
            )
        provisional_channels: dict[str, Any] = {}
        for channel, source_entry in zip(
            geometry.drive_channels, source_entries, strict=True
        ):
            if not isinstance(source_entry, dict):
                raise ValueError("streamed imported Metal source result is invalid")
            channel_context = SolverContext.from_imported_request(
                request, quadrants=quadrants, source_motion=channel.motion
            )
            channel_response = build_provisional_frequency_response(
                index=index,
                frequency_hz=frequency_hz,
                entry=source_entry,
                config=config,
                context=channel_context,
                backend="metal",
                sound_speed_m_per_s=solver_sound_speed_m_per_s(
                    "hornlab_metal_bem"
                ),
            )
            if len(channel.source_ids) > 1:
                channel_response.pop("impedance", None)
            provisional_channels[channel.id] = channel_response
        result_callback(
            index,
            {
                "frequencies": [float(frequency_hz)],
                "channels": provisional_channels,
                "channel_order": channel_order,
                "metadata": {
                    "geometry_type": "imported",
                    "provisional": {
                        "completed_frequency_count": int(index) + 1,
                        "expected_frequency_count": (
                            len(context.frequencies_hz)
                            if context.frequencies_hz is not None
                            else int(context.num_frequencies)
                        ),
                    },
                },
            },
        )
        return True

    observation = _observation(context, msh_text)
    frame_override = _imported_frame(record, context)
    kwargs: dict[str, Any] = {
        "freq_min_hz": context.frequency_range[0],
        "freq_max_hz": context.frequency_range[1],
        "freq_count": context.num_frequencies,
        "freq_spacing": context.frequency_spacing,
        "formulation": DEFAULT_BEM_FORMULATION,
        "complex_k_shift": DEFAULT_COMPLEX_K_SHIFT,
        "observation": observation,
        "progress_callback": progress,
        "mesh_scale": 1.0,
        "native_symmetry_plane": native_symmetry_plane(context),
        "native_check_open_edges": False,
        "mesh_validate": context.mesh_validation_mode != "off",
        "frame_override": frame_override,
        "source_motion": config_motion,
    }
    if result_callback is not None:
        kwargs["on_frequency_result"] = on_frequency_result
    if source_profiles:
        kwargs["source_velocity_profiles"] = source_profiles
    config = _native_config_or_unavailable(kwargs)

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".msh", delete=False, encoding="utf-8"
        ) as handle:
            path = Path(handle.name)
            handle.write(msh_text)

        execution_frequencies = (
            live_execution_frequencies(context).tolist()
            if result_callback is not None
            else list(context.frequencies_hz)
            if context.frequencies_hz is not None
            else None
        )
        results = native_solve_multi_source(
            str(path),
            source_specs,
            config,
            frequencies_hz=execution_frequencies,
        )
        if len(results) != len(geometry.drive_channels):
            raise ValueError(
                "multi-source Metal solver returned a different number of bases than requested"
            )
        sorted_results: dict[str, Any] = {}
        driver_payloads: dict[str, dict[str, Any]] = {}
        for channel, result in zip(geometry.drive_channels, results, strict=True):
            sort_native_result_frequencies(result)
            sorted_results[channel.id] = result
            if channel.driver is not None:
                # Scale before packaging AND before the bases are serialized,
                # so recombination sees the same voltage-driven fields the
                # channel contract reports.
                driver_payloads[channel.id] = _apply_channel_driver(
                    channel,
                    result,
                    record,
                    source_tags,
                    drive_voltage_v=geometry.drive_voltage_v,
                    rg_ohm=geometry.rg_ohm,
                )
            channel_context = SolverContext.from_imported_request(
                request, quadrants=quadrants, source_motion=channel.motion
            )
            channel_metadata = {
                "solver_backend": "metal",
                "solver_mode": "full_3d",
                "geometry_type": "imported",
                "drive_channel_id": channel.id,
                "source_ids": list(channel.source_ids),
                "device_interface": {"selected": "metal", "metal": status},
                "engine": "hornlab-metal-bem",
                "phase_time_convention": "exp(+ikr)",
                "mesh_validation": {
                    "mode": context.mesh_validation_mode,
                    "backend": "hornlab-metal-bem",
                },
                "performance": {
                    "total_time_seconds": time.time() - started,
                    "native_timings": json_safe_native_value(
                        dict(getattr(result, "timings", {}) or {})
                    ),
                },
                "metal": {
                    "solver_mode": "full_3d",
                    "native_symmetry_plane": kwargs["native_symmetry_plane"],
                    "native_check_open_edges": False,
                    "formulation": kwargs["formulation"],
                    "complex_k_shift": kwargs["complex_k_shift"],
                    "solver_log": json_safe_native_value(
                        response_solver_log(getattr(result, "solver_log", []))
                    ),
                },
            }
            channel_response = build_solver_response(
                result=result,
                config=config,
                context=channel_context,
                start_time=started,
                metadata=channel_metadata,
                sound_speed_m_per_s=solver_sound_speed_m_per_s(
                    "hornlab_metal_bem"
                ),
            )
            if len(channel.source_ids) > 1:
                channel_response.pop("impedance", None)
                channel_response["metadata"]["impedance_omitted"] = (
                    "multi-source channel: per-patch impedance is not a channel impedance"
                )
            driver_payload = driver_payloads.get(channel.id)
            if driver_payload is not None:
                electrical = driver_payload.pop("electrical_impedance_ohm")
                channel_response["impedance"] = {
                    "frequencies": electrical["frequencies"],
                    "real": electrical["real"],
                    "imaginary": electrical["imaginary"],
                }
                channel_metadata = channel_response["metadata"]
                channel_metadata["impedance_units"] = "ohms"
                channel_metadata["impedance_quantity"] = "electrical_input_impedance"
                channel_metadata["impedance_drive"] = "voltage"
                channel_metadata["driver"] = driver_payload
                channel_metadata["drive"] = {
                    "voltage_v": geometry.drive_voltage_v,
                    "rg_ohm": geometry.rg_ohm,
                }
                warnings = channel_metadata.setdefault("warnings", [])
                warnings.extend(driver_payload.get("warnings") or [])
                channel_metadata["warning_count"] = len(warnings)
            channels[channel.id] = channel_response
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove temporary Metal mesh %s: %s", path, exc)
    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging imported drive-channel bases")

    per_source_validity = _imported_validity_metadata(record)
    channel_bases_npz = (
        serialize_channel_bases(sorted_results) if len(sorted_results) > 1 else None
    )
    if geometry.combine is not None:
        combined_response = _combined_channel_response(
            geometry=geometry,
            sorted_results=sorted_results,
            request=request,
            quadrants=quadrants,
            config=config,
            config_motion=config_motion,
            started=started,
            status=status,
            kwargs=kwargs,
            per_source_validity=per_source_validity,
        )
        channels[geometry.combine.id] = combined_response
        channel_order.append(geometry.combine.id)
    global_caveat = global_frequency_caveat(request, record)
    fem_volumes = (
        (record.get("evidence") or {}).get("fem_air_volumes")
        if isinstance(record.get("evidence"), Mapping)
        else []
    ) or []
    metadata = {
        "result_contract_version": 2,
        "geometry_type": "imported",
        "solver_backend": "metal",
        "solver_mode": "full_3d",
        "solve_path": "full-3d",
        "axisymmetric_eligibility_reasons": [
            "imported geometry is restricted to Metal full 3-D"
        ],
        "ingest_id": geometry.ingest_id,
        "manifest_sha256": geometry.manifest_sha256,
        "artifact_sha256": geometry.artifact_sha256,
        "tag_namespace": record.get("tag_namespace"),
        "tag_map": json_safe_native_value(record.get("tag_map") or {}),
        "per_source_frequency_validity": per_source_validity,
        "global_frequency_caveat": global_caveat,
        "symmetry_planes_used": sorted(cut_planes),
        "polar_grid_derivation": json_safe_native_value(
            record.get("polar_grid_derivation") or {}
        ),
        "observation_origin_effective": "throat",
        "observation_frame_basis": {
            "axis": json_safe_native_value(frame_override.axis),
            "u": json_safe_native_value(frame_override.u),
            "v": json_safe_native_value(frame_override.v),
            "origin_m": json_safe_native_value(frame_override.origin),
            "mouth_center_m": json_safe_native_value(frame_override.mouth_center),
            "source_center_m": json_safe_native_value(frame_override.source_center),
        },
        "acknowledged_findings": list(geometry.acknowledged_findings),
        "exterior_only": geometry.exterior_only,
        "fem_exclusion": (
            {
                "excluded": True,
                "declared_volume_count": len(fem_volumes),
                "reason": "Phase 2 exterior_only override",
            }
            if fem_volumes and geometry.exterior_only
            else {"excluded": False, "declared_volume_count": len(fem_volumes)}
        ),
        "performance": {"total_time_seconds": time.time() - started},
    }
    envelope: dict[str, Any] = {
        "channels": channels,
        "channel_order": channel_order,
        "metadata": metadata,
    }
    if channel_bases_npz is not None:
        envelope["_channel_bases_npz"] = channel_bases_npz
    return envelope


def _circsym_eligibility_reasons(request: SolveRequest) -> list[str]:
    """Run the mesher/native CircSym probes together, away from the event loop."""

    from . import circsym as circsym_adapter

    reasons: list[str] = []
    if circsym_adapter.circsym_rejection_reasons is None:
        reasons.append("installed mesher does not expose axisymmetric-meridian eligibility")
    else:
        try:
            reasons.extend(
                str(reason)
                for reason in circsym_adapter.circsym_rejection_reasons(
                    _solver_mesher_config(request.design)
                )
            )
        except Exception as exc:
            reasons.append(f"axisymmetric-meridian eligibility check failed: {exc}")

    try:
        context = SolverContext.from_request(request, solver_mode="circsym")
        observation_rejection = circsym_adapter.circsym_observation_rejection_reason(
            context
        )
        if observation_rejection is not None:
            reasons.append(observation_rejection)
    except Exception as exc:
        reasons.append(f"axisymmetric observation eligibility check failed: {exc}")

    status = circsym_adapter.circsym_status()
    if not status["available"] and not reasons:
        reasons.append(str(status["reason"]))
    return reasons


class MetalEngine:
    name = "metal"

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
        result_cb: ResultCallback | None = None,
        imported_record: Mapping[str, Any] | None = None,
    ) -> EngineRunResult:
        if isinstance(request.geometry, ImportedGeometrySource):
            if imported_record is None:
                raise ValueError("imported Metal solve requires its ingestion record")
            execution_msh = imported_record.get("_execution_msh_text")
            if isinstance(execution_msh, str):
                msh_text = verify_record_mesh_text(imported_record, execution_msh)
            else:
                msh_text = await asyncio.to_thread(
                    read_verified_import_mesh, imported_record
                )
            mesh_record = imported_record.get("mesh")
            mesh_stats = (
                dict(mesh_record.get("stats") or {})
                if isinstance(mesh_record, Mapping)
                else {}
            )
            if artifact_cb is not None:
                await artifact_cb(msh_text, mesh_stats)
            cancel_cb()
            results = await asyncio.to_thread(
                solve_imported_metal_from_msh_text,
                msh_text,
                request,
                imported_record,
                stage_callback=stage_cb,
                cancellation_callback=cancel_cb,
                result_callback=result_cb,
            )
            results.setdefault("metadata", {})["mesh_stats"] = mesh_stats
            channel_bases = results.pop("_channel_bases_npz", None)
            return EngineRunResult(
                results=results,
                msh_text=msh_text,
                mesh_stats=mesh_stats,
                channel_bases=channel_bases,
            )

        mode = str(request.design.root.simulation.solver_mode or "auto").strip().lower()
        if mode not in {"auto", "full_3d", "circsym"}:
            raise ValueError("solver_mode must be auto, full_3d, or circsym")

        eligibility_reasons: list[str] = []
        if mode != "full_3d":
            eligibility_reasons = await asyncio.to_thread(
                _circsym_eligibility_reasons, request
            )
            from . import circsym as circsym_adapter

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
                    result_cb=result_cb,
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
            result_callback=result_cb,
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


__all__ = [
    "MetalEngine",
    "MetalUnavailable",
    "metal_status",
    "solve_imported_metal_from_msh_text",
    "solve_metal_from_msh_text",
]
