"""Imported CAD geometry on hornlab-bempp-bem.

Metal solves an ingestion record directly
(``server/solver/metal.py``, ``solve_imported_metal_from_msh_text``). The BEMPP
package takes the same record without a change -- an arbitrary observation
frame, any tag-to-weight drive map and every mirror WG's cutter produces -- so
this module is only the adapter half:

* **Frame.** The record's anchor throat frame is the observation frame, read
  exactly as Metal reads it (:func:`server.solver.imported.imported_anchor_frame`).
* **Channels.** The package has no multi-right-hand-side solve, so each drive
  channel is its own sweep, driving that channel's source tags at unit weight
  -- the drive Metal gives them -- with the channel's own motion. The package
  flips an axial tag that faces back along the axis, as Metal does.
* **Reduced domains.** ``yz``, ``xz`` and ``yz+xz`` all execute natively, from
  the record's domain planes; BEMPP mirrors the y-only half BEAT cannot.
* **Open shells.** BEMPP's P1 space pins the pressure to zero on a free edge,
  where Metal leaves it free, so the two disagree on an open rim. A cut rim on
  a mirror plane is not free. Any other open edge is refused before a job exists
  (:func:`imported_bempp_preflight`), and so is a record too old to say.
* **OpenCL only.** numba is never a shipping backend. BEMPP declares imported
  geometry only while its assembly backend is OpenCL
  (``bempp.geometry_sources_for``), and a solve that would assemble on anything
  else is refused here as well.
"""

from __future__ import annotations

from pathlib import Path
import logging
import tempfile
import time
from typing import Any, Mapping

import numpy as np

from server.jobs.models import ImportedGeometrySource, SolveRequest

from . import bempp
from .acoustics import solver_sound_speed_m_per_s
from .base import CancelCallback, ResultCallback, StageCallback
from .bempp import (
    BEMPP_ADAPTIVE_QUADRATURE_KH_MAX,
    BEMPP_ADAPTIVE_QUADRATURE_KH_MIN,
    BEMPP_ADAPTIVE_QUADRATURE_LOW_ORDER,
    OPENCL_DEVICE_TYPE,
    PREFERRED_ASSEMBLY_BACKEND,
    BemppUnavailable,
)
from .combine import combine_drive_channels, serialize_channel_bases
from .context import SolverContext
from .driver_limits import MemberLimits, member_limits_from_channel
from .field_traces_store import (
    BEMPP_FIELD_TRACE_BACKEND,
    build_field_trace_artifact,
    field_trace_retention_plan,
)
from .formulation import DEFAULT_BEM_FORMULATION, DEFAULT_COMPLEX_K_SHIFT
from .frequency_sweep import live_execution_frequencies, sort_native_result_frequencies
from .imported import (
    imported_anchor_frame,
    imported_domain_planes,
    imported_symmetry_from_cut_planes,
)
from .metal import (
    _apply_channel_driver,
    _channel_basis_metadata,
    _channel_source_identity,
    _imported_validity_metadata,
)
from .result_mapping import (
    build_provisional_frequency_response,
    build_solver_response,
    json_safe_native_value,
    observation_config,
    response_solver_log,
)

logger = logging.getLogger(__name__)

PASSIVE_CARDIOID_REFUSAL = (
    "BEMPP does not solve the passive cardioid; it is solved on Metal only. "
    "Select Metal for this return."
)


def imported_bempp_preflight(record: Mapping[str, Any]) -> str | None:
    """Why BEMPP cannot solve this record, or None when it can.

    Answers only from the record, so the plan and the submission refuse before
    a job exists. An open edge on a mirror plane is the cut rim, which the
    package mirrors; an open edge anywhere else is a free rim, where BEMPP's
    pressure is pinned to zero and Metal's is not.
    """

    mesh = record.get("mesh")
    integrity = mesh.get("integrity") if isinstance(mesh, Mapping) else None
    count = integrity.get("off_plane_open_edge_count") if isinstance(integrity, Mapping) else None
    if isinstance(count, bool) or not isinstance(count, int):
        return (
            "this return's record carries no open-edge count, so BEMPP cannot show "
            "that its mesh has no free rim. Prepare the return again, or select "
            "Metal or BEAT · CPU."
        )
    if count > 0:
        return (
            f"this return has {count} open edge{'s' if count != 1 else ''} off its "
            "mirror planes. BEMPP holds the pressure at zero on a free rim, where "
            "Metal does not, so its answer would differ there. Close the shell in "
            "CAD, or select Metal or BEAT · CPU."
        )
    return None


def _require_opencl(status: Mapping[str, Any]) -> str:
    backend = status.get("assembly_backend")
    if backend != PREFERRED_ASSEMBLY_BACKEND:
        raise BemppUnavailable(
            "BEMPP solves imported CAD geometry on an OpenCL device only. Here it "
            f"would assemble on {backend or 'no backend'}, which is not qualified "
            "for it. Select Metal or BEAT · CPU."
        )
    return str(backend)


def _bempp_section(
    *,
    backend: str,
    native_plane: str | None,
    config: Any,
    tags: list[int] | None,
    motion: str | None,
    result: Any,
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "native_symmetry_plane": native_plane,
        "formulation": json_safe_native_value(getattr(config, "formulation", None)),
        "complex_k_shift": float(getattr(config, "complex_k_shift", DEFAULT_COMPLEX_K_SHIFT)),
        "assembly_backend": backend,
        "opencl_device": getattr(config, "opencl_device", OPENCL_DEVICE_TYPE),
        "precision": getattr(config, "precision", "single"),
        "solver_log": json_safe_native_value(
            response_solver_log(getattr(result, "solver_log", []))
        ),
    }
    if tags is not None:
        section["velocity_source_tags"] = tags
    if motion is not None:
        section["source_motion"] = motion
    return section


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
    backend: str,
    native_plane: str | None,
    per_source_validity: Mapping[str, Any],
    channel_identity: Mapping[str, Mapping[str, Any]],
    member_channels: Mapping[str, Any],
    frame_basis: Mapping[str, Any],
) -> dict[str, Any]:
    """BEMPP's counterpart of Metal's combined channel: the filtered sum."""

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
    frequencies_hz = np.asarray(
        sorted_results[spec.members[0]].frequencies_hz, dtype=np.float64
    ).reshape(-1)
    member_limits: dict[str, MemberLimits] = {}
    for member in spec.members:
        limits = member_limits_from_channel(
            member_channels.get(member),
            frequencies_hz=frequencies_hz,
            max_voltage_v=geometry.max_drive_voltage_v,
        )
        if limits is not None:
            member_limits[member] = limits

    resolved = spec.resolved()
    combined_result, combine_payload = combine_drive_channels(
        sorted_results,
        members=list(spec.members),
        channels=resolved["channels"],
        reference=resolved["reference"],
        member_validity_hz=member_validity_hz,
        member_roles={
            member: channel_identity.get(member, {}).get("role")
            for member in spec.members
        },
        member_limits=member_limits,
    )
    metadata = {
        "solver_backend": "bempp",
        "solver_mode": "full_3d",
        "geometry_type": "imported",
        "drive_channel_id": spec.id,
        "derived_from_channels": list(spec.members),
        "source_ids": [
            source_id
            for member in spec.members
            for source_id in channels_by_id[member].source_ids
        ],
        "device_interface": {
            "selected": f"bempp-cl-{backend}",
            f"bempp-cl-{backend}": dict(status),
        },
        "engine": "hornlab-bempp-bem",
        "phase_time_convention": "exp(+ikr)",
        "combine": combine_payload,
        "mesh_validation": {
            "mode": request.options.mesh_validation_mode,
            "backend": "hornlab-bempp-bem",
        },
        "performance": {"total_time_seconds": time.time() - started},
        "observation_frame_basis": dict(frame_basis),
        "bempp": _bempp_section(
            backend=backend,
            native_plane=native_plane,
            config=config,
            tags=None,
            motion=None,
            result=combined_result,
        ),
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
        sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_bempp_bem"),
    )
    response.pop("impedance", None)
    response["metadata"]["impedance_omitted"] = (
        "combined channel: member drives differ; no single impedance exists"
    )
    return response


def _solve_config(config_kwargs: dict[str, Any]) -> Any:
    try:
        return bempp.SolveConfig(**config_kwargs)
    except TypeError as exc:
        message = str(exc)
        for option, feature in (
            ("velocity_sources", "per-tag velocity sources"),
            ("source_motion", "axial source motion"),
            ("frame_override", "an explicit observation frame"),
            ("on_frequency_result", "streamed frequency results"),
            ("return_surface_traces", "retained surface traces"),
            ("complex_k_shift", "the required complex-k shift"),
            ("formulation", "the required BEM formulation option"),
        ):
            if option in message:
                raise BemppUnavailable(
                    f"Installed hornlab-bempp-bem does not support {feature}."
                ) from exc
        raise


def solve_imported_bempp_from_msh_text(
    msh_text: str,
    request: SolveRequest,
    record: Mapping[str, Any],
    *,
    field_trace_cap_bytes: int | None = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None,
) -> dict[str, Any]:
    """Solve every imported drive channel on BEMPP, one sweep each.

    Returns Metal's imported envelope: a ``multi_channel`` result with one
    contract-shaped channel per drive channel (plus a combined channel when the
    request has one), the channel-bases NPZ under ``_channel_bases_npz`` and
    the surface traces under ``_field_traces``.
    """

    geometry = request.geometry
    if not isinstance(geometry, ImportedGeometrySource):
        raise ValueError("imported BEMPP solve requires imported geometry")
    if request.options.ground_plane.enabled:
        raise BemppUnavailable(
            "The HornLab BEMPP adapter cannot apply a rigid ground plane to "
            "imported geometry."
        )
    if geometry.passive_cardioid_enabled:
        raise BemppUnavailable(PASSIVE_CARDIOID_REFUSAL)
    if (
        not bempp._load_api()
        or bempp.SolveConfig is None
        or bempp.ObservationFrame is None
        or bempp.bempp_solve_frequencies is None
    ):
        raise BemppUnavailable("hornlab-bempp-bem is not installed.")
    status = bempp.bempp_status()
    if not status["available"]:
        raise BemppUnavailable(status["reason"])
    backend = _require_opencl(status)
    refusal = imported_bempp_preflight(record)
    if refusal is not None:
        raise BemppUnavailable(f"BEMPP cannot solve this CAD return: {refusal}")

    domain_planes = imported_domain_planes(record)
    imported_symmetry = imported_symmetry_from_cut_planes(domain_planes)
    quadrants = imported_symmetry.quadrants
    native_plane = imported_symmetry.native_plane
    source_tags = record.get("source_tags")
    if not isinstance(source_tags, Mapping):
        raise ValueError("ingestion record has no source tag map")
    channel_tags: dict[str, list[int]] = {}
    for channel in geometry.drive_channels:
        tags: set[int] = set()
        for source_id in channel.source_ids:
            if source_id not in source_tags:
                raise ValueError(f"ingestion tag map has no active source {source_id!r}")
            tags.add(int(source_tags[source_id]))
        channel_tags[channel.id] = sorted(tags)

    channel_order = [channel.id for channel in geometry.drive_channels]
    started = time.time()
    if stage_callback:
        stage_callback("setup", 0.0, f"Configuring imported BEMPP BEM solve ({backend})")

    motions = {channel.motion for channel in geometry.drive_channels}
    config_motion = next(iter(motions)) if len(motions) == 1 else "normal"
    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion
    )
    context.validate()
    mesh_record = record.get("mesh")
    mesh_record = mesh_record if isinstance(mesh_record, Mapping) else {}
    imported_mesh_stats = mesh_record.get("stats")
    imported_mesh_stats = (
        imported_mesh_stats if isinstance(imported_mesh_stats, Mapping) else None
    )
    frequencies = live_execution_frequencies(context).tolist()
    field_plane_enabled = (
        getattr(context, "polar_config", {}).get("field_plane", True) is True
    )
    retain_traces, trace_reason, trace_estimated_bytes, trace_cap_bytes = (
        field_trace_retention_plan(
            msh_text,
            mesh_stats=imported_mesh_stats,
            frequency_count=len(frequencies),
            channel_count=len(geometry.drive_channels),
            enabled=field_plane_enabled,
            supported=True,
            cap_bytes=field_trace_cap_bytes,
        )
    )
    channel_identity = _channel_source_identity(geometry, record)
    anchor = imported_anchor_frame(record)
    frame_override = bempp.ObservationFrame(**anchor)
    frame_basis = {
        "axis": json_safe_native_value(anchor["axis"]),
        "u": json_safe_native_value(anchor["u"]),
        "v": json_safe_native_value(anchor["v"]),
        "origin_m": json_safe_native_value(anchor["origin"]),
        "mouth_center_m": json_safe_native_value(anchor["mouth_center"]),
        "source_center_m": json_safe_native_value(anchor["source_center"]),
    }
    observation = observation_config(
        context,
        bempp.ObservationConfig,
        BemppUnavailable,
        "hornlab-bempp-bem",
        msh_text=msh_text,
    )
    formulation = DEFAULT_BEM_FORMULATION
    if bempp.BIEFormulation is not None:
        formulation = getattr(bempp.BIEFormulation, "COMPLEX_K", formulation)
    frequency_count = len(frequencies)
    channel_count = len(geometry.drive_channels)
    total_work = max(1, frequency_count * channel_count)

    sorted_results: dict[str, Any] = {}
    configs: dict[str, Any] = {}
    # The runtime keeps one revision per streamed frame and drops any that does
    # not advance it, so frames are numbered across channels.
    next_revision = [0]
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".msh", delete=False, encoding="utf-8"
        ) as handle:
            path = Path(handle.name)
            handle.write(msh_text)
        for channel_index, channel in enumerate(geometry.drive_channels):
            channel_context = SolverContext.from_imported_request(
                request, quadrants=quadrants, source_motion=channel.motion
            )
            holder: dict[str, Any] = {}

            def progress(
                index: int,
                total: int,
                frequency_hz: float,
                *,
                _offset: int = channel_index * frequency_count,
                _channel_index: int = channel_index,
                _channel_id: str = channel.id,
            ) -> None:
                del frequency_hz
                if cancellation_callback:
                    cancellation_callback()
                if stage_callback:
                    stage_callback(
                        "frequency_solve",
                        (_offset + index + 1) / total_work,
                        f"Solving frequency {index + 1}/{total} of drive channel "
                        f"{_channel_index + 1}/{channel_count} ({_channel_id}) "
                        "with BEMPP BEM",
                    )

            def on_frequency_result(
                index: int,
                frequency_hz: float,
                entry: dict[str, Any],
                *,
                _channel_index: int = channel_index,
                _channel: Any = channel,
                _context: SolverContext = channel_context,
                _holder: dict[str, Any] = holder,
            ) -> bool:
                if cancellation_callback:
                    cancellation_callback()
                if result_callback is None:
                    return True
                channel_response = build_provisional_frequency_response(
                    index=index,
                    frequency_hz=frequency_hz,
                    entry=entry,
                    config=_holder["config"],
                    context=_context,
                    backend="bempp",
                    sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_bempp_bem"),
                )
                if len(_channel.source_ids) > 1:
                    channel_response.pop("impedance", None)
                channel_metadata = channel_response.setdefault("metadata", {})
                channel_metadata.update(channel_identity[_channel.id])
                channel_metadata["observation_frame_basis"] = dict(frame_basis)
                revision = next_revision[0]
                next_revision[0] += 1
                frame: dict[str, Any] = {
                    "result_kind": "multi_channel",
                    "result_contract_version": 2,
                    "channels": {_channel.id: channel_response},
                    "channel_order": channel_order,
                    "metadata": {
                        "geometry_type": "imported",
                        # Channels arrive one after another, so the count is
                        # the current channel's, out of the sweep.
                        "provisional": {
                            "completed_frequency_count": int(index) + 1,
                            "expected_frequency_count": frequency_count,
                            "channel": {
                                "id": _channel.id,
                                "index": _channel_index + 1,
                                "count": channel_count,
                            },
                        },
                    },
                }
                if _channel_index == 0:
                    # The envelope's frequency axis is the sweep's: each
                    # frequency once, from the first channel's frames.
                    frame["frequencies"] = [float(frequency_hz)]
                result_callback(revision, frame)
                return True

            config_kwargs: dict[str, Any] = {
                "freq_min_hz": context.frequency_range[0],
                "freq_max_hz": context.frequency_range[1],
                "freq_count": context.num_frequencies,
                "freq_spacing": context.frequency_spacing,
                "formulation": formulation,
                "complex_k_shift": DEFAULT_COMPLEX_K_SHIFT,
                "observation": observation,
                "frame_override": frame_override,
                "velocity_sources": {tag: 1.0 for tag in channel_tags[channel.id]},
                "source_motion": channel.motion,
                "progress_callback": progress,
                "mesh_scale": 1.0,
                "native_symmetry_plane": native_plane,
                "assembly_backend": backend,
                "opencl_device": OPENCL_DEVICE_TYPE,
                "precision": "single",
                "adaptive_quadrature": True,
                "adaptive_quadrature_kh_min": BEMPP_ADAPTIVE_QUADRATURE_KH_MIN,
                "adaptive_quadrature_kh_max": BEMPP_ADAPTIVE_QUADRATURE_KH_MAX,
                "adaptive_quadrature_low_order": BEMPP_ADAPTIVE_QUADRATURE_LOW_ORDER,
                "return_surface_traces": retain_traces,
            }
            if result_callback is not None:
                config_kwargs["on_frequency_result"] = on_frequency_result
            config = _solve_config(config_kwargs)
            holder["config"] = config
            # The preflight already holds the rim to the record's evidence: any
            # open edge left lies on a mirror plane.
            config.require_closed_mesh = False
            # One cancellable process that streams; Stop kills the worker.
            config.workers = 1
            result = bempp.bempp_solve_frequencies(str(path), frequencies, config)
            sort_native_result_frequencies(result)
            bempp._refuse_silent_zero_result(result, backend)
            sorted_results[channel.id] = result
            configs[channel.id] = config
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove temporary BEMPP mesh %s: %s", path, exc)

    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging imported drive-channel bases")

    driver_payloads: dict[str, dict[str, Any]] = {}
    for channel in geometry.drive_channels:
        if channel.driver is None:
            continue
        result = sorted_results[channel.id]
        # The driver model reads the driven source's average surface pressure,
        # which BEMPP reports as ``impedance``; NaN must not become a driver answer.
        if not np.all(np.isfinite(np.asarray(result.impedance, dtype=np.complex128))):
            raise BemppUnavailable(
                f"BEMPP returned no source-average pressure for drive channel "
                f"{channel.id!r}, which its driver model needs."
            )
        driver_payloads[channel.id] = _apply_channel_driver(
            channel,
            result,
            record,
            source_tags,
            drive_voltage_v=geometry.drive_voltage_v,
            rg_ohm=geometry.rg_ohm,
        )

    channels: dict[str, Any] = {}
    for channel in geometry.drive_channels:
        result = sorted_results[channel.id]
        channel_context = SolverContext.from_imported_request(
            request, quadrants=quadrants, source_motion=channel.motion
        )
        channel_metadata = {
            "solver_backend": "bempp",
            "solver_mode": "full_3d",
            "geometry_type": "imported",
            "drive_channel_id": channel.id,
            "source_ids": list(channel.source_ids),
            **channel_identity[channel.id],
            "device_interface": {
                "selected": f"bempp-cl-{backend}",
                f"bempp-cl-{backend}": dict(status),
            },
            "engine": "hornlab-bempp-bem",
            "phase_time_convention": "exp(+ikr)",
            "assembly_backend": backend,
            "mesh_validation": {
                "mode": context.mesh_validation_mode,
                "backend": "hornlab-bempp-bem",
            },
            "performance": {
                "total_time_seconds": time.time() - started,
                "native_timings": json_safe_native_value(
                    dict(getattr(result, "timings", {}) or {})
                ),
            },
            "observation_frame_basis": dict(frame_basis),
            "bempp": _bempp_section(
                backend=backend,
                native_plane=native_plane,
                config=configs[channel.id],
                tags=channel_tags[channel.id],
                motion=channel.motion,
                result=result,
            ),
        }
        channel_response = build_solver_response(
            result=result,
            config=configs[channel.id],
            context=channel_context,
            start_time=started,
            metadata=channel_metadata,
            sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_bempp_bem"),
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
            response_metadata = channel_response["metadata"]
            response_metadata["impedance_units"] = "ohms"
            response_metadata["impedance_quantity"] = "electrical_input_impedance"
            response_metadata["impedance_phase_convention"] = "engineering_exp_plus_jwt"
            response_metadata["impedance_drive"] = "voltage"
            response_metadata["driver"] = driver_payload
            response_metadata["drive"] = {
                "voltage_v": geometry.drive_voltage_v,
                "rg_ohm": geometry.rg_ohm,
            }
            warnings = response_metadata.setdefault("warnings", [])
            warnings.extend(driver_payload.get("warnings") or [])
            response_metadata["warning_count"] = len(warnings)
        channels[channel.id] = channel_response

    per_source_validity = _imported_validity_metadata(record)
    channel_bases_npz = serialize_channel_bases(
        sorted_results,
        metadata_by_id=_channel_basis_metadata(
            geometry, record, source_tags, driver_payloads
        ),
    )
    first_config = configs[geometry.drive_channels[0].id]
    if geometry.combine is not None:
        channels[geometry.combine.id] = _combined_channel_response(
            geometry=geometry,
            sorted_results=sorted_results,
            request=request,
            quadrants=quadrants,
            config=first_config,
            config_motion=config_motion,
            started=started,
            status=status,
            backend=backend,
            native_plane=native_plane,
            per_source_validity=per_source_validity,
            channel_identity=channel_identity,
            member_channels=channels,
            frame_basis=frame_basis,
        )
        channel_order.append(geometry.combine.id)
    fem_volumes = (
        (record.get("evidence") or {}).get("fem_air_volumes")
        if isinstance(record.get("evidence"), Mapping)
        else []
    ) or []
    metadata = {
        "result_contract_version": 2,
        "geometry_type": "imported",
        "solver_backend": "bempp",
        "solver_mode": "full_3d",
        "solve_path": "full-3d",
        "axisymmetric_eligibility_reasons": ["imported geometry solves full 3-D only"],
        "solver_engine": {
            "engine": "bempp",
            "package": "hornlab-bempp-bem",
            "package_version": status.get("version"),
            "device": OPENCL_DEVICE_TYPE,
            "assembly_backend": backend,
            "formulation": json_safe_native_value(formulation),
            "complex_k_shift": DEFAULT_COMPLEX_K_SHIFT,
        },
        "ingest_id": geometry.ingest_id,
        "manifest_sha256": geometry.manifest_sha256,
        "artifact_sha256": geometry.artifact_sha256,
        "tag_namespace": record.get("tag_namespace"),
        "tag_map": json_safe_native_value(record.get("tag_map") or {}),
        "per_source_frequency_validity": per_source_validity,
        "symmetry_planes_used": sorted(domain_planes),
        "polar_grid_derivation": json_safe_native_value(
            record.get("polar_grid_derivation") or {}
        ),
        "observation_origin_effective": "throat",
        "observation_frame_basis": dict(frame_basis),
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
        "field_trace_retention": {
            "estimated_bytes": trace_estimated_bytes,
            "cap_bytes": trace_cap_bytes,
        },
    }
    envelope: dict[str, Any] = {
        "result_kind": "multi_channel",
        "result_contract_version": 2,
        "channels": channels,
        "channel_order": channel_order,
        "metadata": metadata,
    }
    envelope_frequencies = sorted(
        {
            float(value)
            for channel_payload in channels.values()
            for value in (channel_payload.get("frequencies") or [])
        }
    )
    if envelope_frequencies:
        envelope["frequencies"] = envelope_frequencies
    if channel_bases_npz is not None:
        envelope["_channel_bases_npz"] = channel_bases_npz
    field_traces = (
        build_field_trace_artifact(
            msh_text,
            [(channel.id, sorted_results[channel.id]) for channel in geometry.drive_channels],
            first_config,
            backend=BEMPP_FIELD_TRACE_BACKEND,
            sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_bempp_bem"),
        )
        if retain_traces
        else None
    )
    if retain_traces and field_traces is None:
        trace_reason = "trace_output_missing"
    envelope["_field_traces"] = field_traces
    envelope["_field_trace_unavailable_reason"] = trace_reason
    return envelope


__all__ = [
    "PASSIVE_CARDIOID_REFUSAL",
    "imported_bempp_preflight",
    "solve_imported_bempp_from_msh_text",
]
