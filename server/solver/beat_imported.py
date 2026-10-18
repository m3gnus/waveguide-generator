"""Imported CAD geometry through official BEAT compiled systems.

The ingestion mesh and its observation frame are retained directly. Source
bases and channel ports express normal/axial motion without rewriting tags or
rotating the mesh. WG maps unit-acceleration pressure, historical pressure
loading, adaptive sweeps and retained traces back to the result contract.
Preflight refuses unsupported symmetry, rigid ground and passive cardioid
campaigns before submission. Explicit exterior-transducer jobs use their own
voltage-driven v3 contract.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping, Sequence

import numpy as np

from server.contracts.conventions import (
    ENGINEERING_PHASE_CONVENTION,
    PHASE_TIME_CONVENTION,
)
from server.jobs.models import ImportedGeometrySource, SolveRequest

from .beat_runtime.warm_cache import signature_scope as solve_signature_scope
from .base import CancelCallback, ResultCallback, StageCallback
from .beat import (
    BeatUnavailable,
    announce_beat_warmup_wait,
    beat_engine_name,
)
from .combine import serialize_channel_bases
from .combined_channel import package_combined_channel
from .context import SolverContext
from .field_traces_store import (
    BEAT_FIELD_TRACE_BACKEND,
    build_field_trace_artifact,
    field_trace_retention_plan,
)
from .adaptive_sweep import (
    enabled as adaptive_enabled,
    native_acquisition_frequencies,
    solve_native_adaptively,
)
from .frequency_sweep import live_execution_frequencies
from .imported import (
    imported_domain_planes,
    imported_symmetry_from_cut_planes,
    prepare_axial_drive,
)
from .imported_channels import (
    apply_channel_driver,
    channel_basis_metadata,
    channel_source_identity,
    imported_validity_metadata,
)
from .result_mapping import (
    build_provisional_frequency_response,
    build_solver_response,
    json_safe_native_value,
    response_solver_log,
)


_DRIVE_CONVENTION = (
    "q=i*rho*omega*v_n on a 1 m/s velocity basis, rescaled to unit normal "
    "acceleration by the package"
)

PASSIVE_CARDIOID_REFUSAL = (
    "The passive-cardioid radiation campaign is implemented on Metal only. "
    "Turn the passive cardioid off to solve this return on BEAT, or solve it "
    "with Metal."
)


class ImportedBeatRefusal(ValueError):
    """A return BEAT cannot solve as prepared, with the reason named."""


def compile_imported_beat(
    record: Mapping[str, Any], msh_text: str, drive_channels: Sequence[Any] | None = None,
    *, request: SolveRequest | None = None, backend: str = "cpu", precision: str = "float32",
    surface_traces: bool = False,
):
    """Compile or refuse, without runtime discovery, for admission and execution.

    The optional request retains the historical record-only preflight interface
    for qualification scripts. Production always supplies the actual request.
    """
    from server.jobs.models import DriveChannel
    from .beat_adapter.request import build_imported_request, validate_imported_request

    symmetry = imported_symmetry_from_cut_planes(imported_domain_planes(record))
    if request is not None:
        if request.options.ground_plane.enabled:
            raise ImportedBeatRefusal(
                "The HornLab BEAT adapter cannot apply a rigid ground plane to imported geometry.")
        if request.geometry.passive_cardioid_enabled:
            raise ImportedBeatRefusal(PASSIVE_CARDIOID_REFUSAL)
        drive_channels = request.geometry.drive_channels
        context = SolverContext.from_imported_request(request, quadrants=symmetry.quadrants,
                                                      source_motion="normal")
    else:
        context = SolverContext(None, (100., 1000.), 2, quadrants=symmetry.quadrants)
        if drive_channels is None:
            drive_channels = [DriveChannel(id="preflight", source_ids=list(record.get("source_tags") or {}))]
    channels = [channel if isinstance(channel, DriveChannel) else DriveChannel.model_validate(channel)
                for channel in drive_channels]
    try:
        compiled = build_imported_request(
            msh_text, context, record, channels, backend=backend, engine_id=beat_engine_name(backend),
            precision=precision, surface_traces=surface_traces)
        validate_imported_request(compiled, symmetry.native_plane)
        return compiled
    except ValueError as exc:
        message = str(exc).replace("right-handed and orthonormal", "right-handed orthonormal")
        raise ImportedBeatRefusal(message) from exc


def imported_beat_preflight(
    record: Mapping[str, Any], msh_text: str, drive_channels: Sequence[Any] | None = None,
    *, request: SolveRequest | None = None, backend: str = "cpu",
) -> str | None:
    """The execution compiler's verdict, without importing or running BEAT."""
    try:
        compile_imported_beat(record, msh_text, drive_channels, request=request, backend=backend)
    except ValueError as exc:
        return str(exc)
    return None


def _frame_basis(parts: Mapping[str, Any]) -> dict[str, list[float]]:
    return {
        key: [float(value) for value in parts[name]]
        for key, name in (
            ("axis", "axis"),
            ("u", "u"),
            ("v", "v"),
            ("origin_m", "origin"),
            ("mouth_center_m", "mouth_center"),
            ("source_center_m", "source_center"),
        )
    }


def _beat_section(
    *,
    backend: str,
    native_plane: str | None,
    merged_tags: list[int] | None,
    result: Any,
    precision: str = "single",
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "native_symmetry_plane": native_plane,
        "formulation": "burton_miller",
        "backend": backend,
        "drive_convention": _DRIVE_CONVENTION,
        "precision": precision,
        "solver_log": json_safe_native_value(
            response_solver_log(getattr(result, "solver_log", []))
        ),
    }
    if merged_tags is not None:
        # Retain the persisted source-tag provenance for this channel.
        section["merged_source_tags"] = merged_tags
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
    """BEAT's counterpart of Metal's combined channel: the filtered sum."""

    spec = geometry.combine
    assert spec is not None

    def beat_metadata(combined_result: Any) -> dict[str, Any]:
        return {
            "solver_backend": "beat",
            "device_interface": {
                "selected": beat_engine_name(backend),
                beat_engine_name(backend): dict(status),
            },
            "engine": "beat-engine",
            "mesh_validation": {
                "mode": request.options.mesh_validation_mode,
                "backend": "beat-engine",
            },
            "performance": {"total_time_seconds": time.time() - started},
            "observation_frame_basis": dict(frame_basis),
            "beat": _beat_section(
                backend=backend,
                native_plane=native_plane,
                merged_tags=None,
                result=combined_result,
            ),
        }

    return package_combined_channel(
        spec=spec,
        drive_channels=geometry.drive_channels,
        results_by_id=sorted_results,
        member_channels=member_channels,
        per_source_validity=per_source_validity,
        member_roles={
            member: channel_identity.get(member, {}).get("role") for member in spec.members
        },
        max_drive_voltage_v=geometry.max_drive_voltage_v,
        frequencies_hz=sorted_results[spec.members[0]].frequencies_hz,
        request=request,
        quadrants=quadrants,
        config_motion=config_motion,
        config=config,
        start_time=started,
        sound_speed_m_per_s=(343.0),
        backend_metadata=beat_metadata,
    )


@solve_signature_scope
def solve_imported_beat_from_msh_text(
    msh_text: str,
    request: SolveRequest,
    record: Mapping[str, Any],
    *,
    backend: str,
    field_trace_cap_bytes: int | None = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None,
    _worker_manager: Any = None,
    _julia_executable: str | None = None, _precision: str = "float32",
    _native_result_callback: Any = None,
) -> dict[str, Any]:
    """Solve imported drive channels from one compiled BEAT topology.

    Returns Metal's imported envelope: a ``multi_channel`` result with one
    contract-shaped channel per drive channel (plus a combined channel when the
    request has one), the channel-bases NPZ under ``_channel_bases_npz`` and
    the surface traces under ``_field_traces``.

    Internal conformance hooks mirror the parametric selector/runtime overrides
    and expose unrounded native channel fields; ordinary callers are unchanged.
    """

    from .beat_transducer_imported import enabled, solve

    if isinstance(request.geometry, ImportedGeometrySource) and enabled(request.geometry.drive_channels):
        return solve(msh_text, request, record, backend=backend,
                     stage_callback=stage_callback, cancellation_callback=cancellation_callback,
                     result_callback=result_callback, worker_manager=_worker_manager,
                     julia_executable=_julia_executable)
    profile = None
    from .beat_runtime.profile import start_profile

    profile = start_profile(logging.getLogger(__name__).info, "wg")
    precision = "double" if _precision == "float64" else "single"
    geometry = request.geometry
    if not isinstance(geometry, ImportedGeometrySource):
        raise ValueError("imported BEAT solve requires imported geometry")
    from .official_beat import (
        batch_request, production_statuses, response_config, solve_compiled, sort_official_result,
        common_artifact_results,
    )

    status = production_statuses(backend=backend).get(backend)
    if profile is not None:
        profile.mark("statuses done")
    if status is None:
        raise BeatUnavailable(f"Unknown BEAT backend {backend!r}")
    if not status["available"]:
        raise BeatUnavailable(status["reason"])

    domain_planes = imported_domain_planes(record)
    imported_symmetry = imported_symmetry_from_cut_planes(domain_planes)
    quadrants = imported_symmetry.quadrants
    native_plane = imported_symmetry.native_plane
    try:
        compiled = compile_imported_beat(record, msh_text, request=request, backend=backend,
                                         precision=_precision)
    except ValueError as exc:
        raise BeatUnavailable(str(exc)) from exc
    if profile is not None:
        profile.mark("request built")
    source_tags = record["source_tags"]
    channel_tags = {channel.id: frozenset(basis.tag for basis in compiled.bases
                                        if basis.port_id in compiled.channel_ports[channel.id])
                    for channel in geometry.drive_channels}
    _, axial_identity = prepare_axial_drive(record, msh_text, geometry.drive_channels)

    channel_order = [channel.id for channel in geometry.drive_channels]
    started = time.time()
    if stage_callback:
        stage_callback("setup", 0.0, f"Configuring imported BEAT Engine BEM solve ({backend})")
    announce_beat_warmup_wait(stage_callback)

    motions = {channel.motion for channel in geometry.drive_channels}
    config_motion = next(iter(motions)) if len(motions) == 1 else "normal"
    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion
    )
    context.validate()
    mesh_record = record.get("mesh")
    mesh_record = mesh_record if isinstance(mesh_record, Mapping) else {}
    imported_mesh_stats = mesh_record.get("stats")
    imported_mesh_stats = imported_mesh_stats if isinstance(imported_mesh_stats, Mapping) else None
    frequencies = live_execution_frequencies(context).tolist()
    field_plane_enabled = getattr(context, "polar_config", {}).get("field_plane", True) is True
    retain_traces, trace_reason, trace_estimated_bytes, trace_cap_bytes = (
        field_trace_retention_plan(
            msh_text,
            mesh_stats=imported_mesh_stats,
            frequency_count=len(frequencies),
            channel_count=len(geometry.drive_channels),
            enabled=field_plane_enabled,
            supported=bool(status.get("surface_traces")),
            cap_bytes=field_trace_cap_bytes,
            unsupported_reason="unsupported_solver_version",
        )
    )
    from dataclasses import replace
    from .beat_adapter.results import result_outputs

    compiled = replace(compiled, surface_traces=retain_traces, wire={
        **compiled.wire, "outputs": result_outputs(compiled.layout, surface_traces=retain_traces, driver_loading=True)})
    channel_identity = channel_source_identity(geometry, record, axial_identity)
    frame_basis = _frame_basis(compiled.frame)
    frequency_count = len(frequencies)
    channel_count = len(geometry.drive_channels)
    adaptive = adaptive_enabled(context)
    acquisition_count = (
        len(native_acquisition_frequencies(context)) if adaptive else frequency_count
    )
    total_work = max(1, acquisition_count * channel_count)

    def stage_status(message: str) -> None:
        if stage_callback and message:
            stage_callback(
                "frequency_solve" if adaptive else "setup",
                work_done / total_work if adaptive else 0.0,
                message,
            )

    sorted_results: dict[str, Any] = {}
    configs: dict[str, Any] = {}
    work_done = 0
    # The runtime keeps one revision per streamed frame and drops any that
    # does not advance it, so frames are numbered across channels.
    next_revision = [0]
    live_channel_status: dict[str, list[str]] = {}
    # Adaptive channels retain independent refinement criteria and acquisition axes.
    # Fixed grids share the operator and independent excitation columns.
    joint = (not adaptive) and (channel_count > 1)
    joint_callbacks = {}
    joint_compiled = compiled if joint else None
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
            _offset: int = work_done,
            _channel_index: int = channel_index,
            _channel_id: str = channel.id,
        ) -> None:
            nonlocal work_done
            del frequency_hz
            if adaptive:
                work_done += 1
                index, total = work_done - _offset - 1, acquisition_count
            completed = (work_done if adaptive else
                         (index * channel_count + _channel_index + 1 if joint
                          else _offset + index + 1))
            if stage_callback:
                stage_callback(
                    "frequency_solve",
                    completed / total_work,
                    f"Solving frequency {index + 1}/{total} of drive channel "
                    f"{_channel_index + 1}/{channel_count} ({_channel_id}) "
                    "with BEAT Engine" + (" (native acquisition)" if adaptive else ""),
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
            if result_callback is None:
                return True
            pressure = np.asarray(
                entry.get("observation_pressure_complex"), dtype=np.complex128
            )
            impedance = entry.get("impedance")
            impedance = None if impedance is None else complex(impedance)
            with np.errstate(divide="ignore"):
                spl = 20.0 * np.log10(np.abs(pressure) / 20.0e-6)
            channel_response = build_provisional_frequency_response(
                index=index,
                frequency_hz=frequency_hz,
                entry={
                    "observation_angles_deg": entry.get("observation_angles_deg"),
                    "observation_planes": entry.get("observation_planes"),
                    "observation_spl_db": spl,
                    "observation_pressure_complex": pressure,
                    "impedance": impedance,
                },
                config=_holder["config"],
                context=_context,
                backend="beat",
                sound_speed_m_per_s=(343.0),
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
                    # The count describes this channel on the requested sweep.
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

        config = response_config(compiled, channel_context)
        holder["config"] = config
        if joint:
            joint_callbacks[channel.id] = (progress, on_frequency_result if result_callback else None)
            configs[channel.id] = config
            continue

        try:
            if adaptive:

                def publish(native):
                    if result_callback is None:
                        return
                    snapshot = build_solver_response(
                        result=native,
                        config=config,
                        context=channel_context,
                        start_time=started,
                        metadata={
                            **channel_identity[channel.id],
                            "observation_frame_basis": dict(frame_basis),
                        },
                        sound_speed_m_per_s=(343.0),
                    )
                    if len(channel.source_ids) > 1:
                        snapshot.pop("impedance", None)
                    live_channel_status[channel.id] = snapshot["frequency_status"]
                    # Unpublished channels have no solved rows yet.
                    flags = [
                        "solved"
                        if all(
                            member in live_channel_status
                            and live_channel_status[member][i] == "solved"
                            for member in channel_order
                        )
                        else "interpolated"
                        for i in range(frequency_count)
                    ]
                    frame = {
                        "result_kind": "multi_channel",
                        "result_contract_version": 2,
                        "channels": {channel.id: snapshot},
                        "channel_order": channel_order,
                        "frequencies": snapshot["frequencies"],
                        "frequency_status": flags,
                        "metadata": {
                            "geometry_type": "imported",
                            "observation_frame_basis": dict(frame_basis),
                            "provisional": {
                                "completed_frequency_count": native.adaptive_sampling[
                                    "solved_count"
                                ],
                                "expected_frequency_count": frequency_count,
                                "channel": {
                                    "id": channel.id,
                                    "index": channel_index + 1,
                                    "count": channel_count,
                                },
                            },
                        },
                    }
                    result_callback(next_revision[0], frame)
                    next_revision[0] += 1

                def solve_batch(batch):
                    # Reuse the compiled topology, exciting only this channel's
                    # ports while its independent adaptive sweep refines.
                    selected = replace(compiled,
                                       channel_ports={channel.id: compiled.channel_ports[channel.id]},
                                       channel_loading={channel.id: compiled.channel_loading[channel.id]},
                                       wire={**compiled.wire, "excitation_port_ids":
                                             list(compiled.channel_ports[channel.id])})
                    return solve_compiled(
                        batch_request(selected, batch), channel_id=channel.id,
                        worker_manager=_worker_manager, julia_executable=_julia_executable,
                        _profile=profile,
                        cancellation_callback=cancellation_callback,
                        progress_callback=progress, status_callback=stage_status,
                    )
                result = solve_native_adaptively(
                    context, solve_batch, distance_m=config.observation.distance_m,
                    sound_speed=(343.0),
                    publish=publish, cancel=cancellation_callback,
                )
            else:
                result = solve_compiled(
                    batch_request(compiled, frequencies), channel_id=channel.id,
                    worker_manager=_worker_manager, julia_executable=_julia_executable,
                    _profile=profile,
                    cancellation_callback=cancellation_callback, progress_callback=progress,
                    on_frequency_result=on_frequency_result if result_callback else None,
                    status_callback=stage_status,
                )
        except NotImplementedError as exc:
            raise BeatUnavailable(str(exc)) from exc
        sort_official_result(result)
        if not adaptive:
            work_done += frequency_count
        if joint:
            continue
        sorted_results[channel.id] = result
        configs[channel.id] = holder["config"]
        if (getattr(sorted_results[channel.id], "cancelled", False)):
            break

    if joint:
        sorted_results = solve_compiled(
            joint_compiled, channel_callbacks=joint_callbacks,
            worker_manager=_worker_manager, julia_executable=_julia_executable,
            _profile=profile, cancellation_callback=cancellation_callback,
            status_callback=stage_status,
        )
        for result in sorted_results.values():
            sort_official_result(result)

    cancelled = (any(getattr(r, "cancelled", False) for r in sorted_results.values()))
    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging imported drive-channel bases")

    driver_payloads: dict[str, dict[str, Any]] = {}
    for channel in geometry.drive_channels:
        if channel.id not in sorted_results:
            continue
        if channel.driver is None:
            continue
        result = sorted_results[channel.id]
        # The driver model reads the driven source's average surface pressure,
        # which BEAT reports as ``impedance``. A solve that returned none
        # cannot be voltage-driven, and NaN must not become a driver answer.
        if not np.all(np.isfinite(np.asarray(result.impedance, dtype=np.complex128))):
            raise BeatUnavailable(
                f"BEAT returned no source-average pressure for drive channel "
                f"{channel.id!r}, which its driver model needs."
            )
        driver_payloads[channel.id] = apply_channel_driver(
            channel,
            result,
            record,
            source_tags,
            drive_voltage_v=geometry.drive_voltage_v,
            rg_ohm=geometry.rg_ohm,
        )

    channels: dict[str, Any] = {}
    for channel in geometry.drive_channels:
        if channel.id not in sorted_results:
            continue
        result = sorted_results[channel.id]
        channel_context = SolverContext.from_imported_request(
            request, quadrants=quadrants, source_motion=channel.motion
        )
        if _native_result_callback is not None:
            _native_result_callback(channel.id, result)
        channel_metadata = {
            "solver_backend": "beat",
            "solver_mode": "full_3d",
            "geometry_type": "imported",
            "drive_channel_id": channel.id,
            "source_ids": list(channel.source_ids),
            **channel_identity[channel.id],
            "device_interface": {
                "selected": beat_engine_name(backend),
                beat_engine_name(backend): status,
            },
            "engine": "beat-engine",
            "solver_engine": {
                "engine": beat_engine_name(backend),
                "provider": "official",
                "package": "beat-engine",
                "package_version": status.get("version"),
                "device": backend,
            },
            "phase_time_convention": PHASE_TIME_CONVENTION,
            "mesh_validation": {
                "mode": context.mesh_validation_mode,
                "backend": "beat-engine",
            },
            "performance": {
                "total_time_seconds": time.time() - started,
                "native_timings": json_safe_native_value(
                    dict(getattr(result, "timings", {}) or {})
                ),
            },
            # Set before the response is built, so the record's frame is
            # reported and not BEAT's rotated +z frame.
            "observation_frame_basis": dict(frame_basis),
            "beat": _beat_section(
                backend=backend,
                native_plane=native_plane,
                merged_tags=sorted(channel_tags[channel.id]),
                result=result,
                precision=precision,
            ),
        }
        channel_response = build_solver_response(
            result=result,
            config=configs[channel.id],
            context=channel_context,
            start_time=started,
            metadata=channel_metadata,
            sound_speed_m_per_s=(343.0),
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
            response_metadata["impedance_phase_convention"] = ENGINEERING_PHASE_CONVENTION
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

    per_source_validity = imported_validity_metadata(record)
    artifact_results = common_artifact_results(sorted_results) if cancelled else sorted_results
    if cancelled:
        channel_order = [name for name in channel_order if name in channels]
    basis_metadata = channel_basis_metadata(
        geometry, record, source_tags, driver_payloads, axial_identity)
    basis_metadata = {name: value for name, value in basis_metadata.items() if name in artifact_results}
    channel_bases_npz = serialize_channel_bases(
        artifact_results,
        metadata_by_id=basis_metadata,
    )
    first_config = configs[geometry.drive_channels[0].id]
    if geometry.combine is not None and not cancelled:
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
        "solver_backend": "beat",
        "solver_mode": "full_3d",
        "solve_path": "full-3d",
        "solver_engine": {
            "engine": beat_engine_name(backend),
            "provider": "official",
            "package": "beat-engine",
            "package_version": status.get("version"),
            "device": backend,
            "formulation": "burton_miller",
            "precision": precision,
        },
        "ingest_id": geometry.ingest_id,
        "manifest_sha256": geometry.manifest_sha256,
        "artifact_sha256": geometry.artifact_sha256,
        "tag_namespace": record.get("tag_namespace"),
        "tag_map": json_safe_native_value(record.get("tag_map") or {}),
        "per_source_frequency_validity": per_source_validity,
        "symmetry_planes_used": sorted(domain_planes),
        "polar_grid_derivation": json_safe_native_value(record.get("polar_grid_derivation") or {}),
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
    metadata["cancelled"] = cancelled
    metadata["beat_solver_frame"] = {
        "rotation_rows": np.eye(3).tolist(),
        "origin_m": [float(value) for value in compiled.frame["origin"]],
        "note": "Official BEAT uses the ingestion mesh and observation frame directly.",
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
    if adaptive_enabled(context) and not cancelled:
        envelope["frequency_status"] = [
            "solved"
            if all(payload["frequency_status"][i] == "solved" for payload in channels.values())
            else "interpolated"
            for i in range(len(envelope_frequencies))
        ]
    if channel_bases_npz is not None:
        envelope["_channel_bases_npz"] = channel_bases_npz
    # Surface traces retain the ingestion mesh vertex and face order: the
    # same mesh the job stores and the viewport draws.
    field_traces = (
        build_field_trace_artifact(
            msh_text,
            list(artifact_results.items()),
            first_config,
            backend=BEAT_FIELD_TRACE_BACKEND,
            sound_speed_m_per_s=(343.0),
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
    "ImportedBeatRefusal", "PASSIVE_CARDIOID_REFUSAL", "compile_imported_beat",
    "imported_beat_preflight", "solve_imported_beat_from_msh_text",
]
