"""Explicit voltage-driven CAD jobs on the official BEAT v3 contract.

Legacy CAD/Boundary Lab paths never enter this module. The first adoption is
complete outward solids, no images, bare drivers and independently driven RMS
voltage bases with all other transducers shorted.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from urllib.parse import quote
import time

import numpy as np

from server.contracts.conventions import (
    SOLVER_TIME_CONVENTION,
    PHASE_TIME_CONVENTION,
    ENGINEERING_PHASE_CONVENTION,
    solver_to_engineering,
)
from typing import Any
from .beat_adapter.mesh import read_surface
from .beat_adapter.request import _layout
from .beat_adapter.results import SweepResult
from .beat_adapter.transducers import ExteriorDriver, TransducerSweep, build_transducer_request
from .beat_runtime.provider import official_selected
from .context import SolverContext
from .frequency_sweep import canonical_frequencies
from .imported import imported_anchor_frame, imported_domain_planes
from .imported_channels import channel_source_identity
from .result_mapping import build_solver_response, json_safe_native_value


def enabled(channels: Sequence) -> bool:
    return any(getattr(channel, "exterior_transducer", None) is not None for channel in channels)


def preflight(record: Mapping, msh_text: str, channels: Sequence, *, backend: str) -> str | None:
    try:
        if not official_selected() or backend not in {"cpu", "metal"}:
            raise ValueError("Exterior transducers require the official BEAT CPU or Metal runtime")
        if imported_domain_planes(record):
            raise ValueError("Exterior transducers require complete solids without symmetry cuts")
        if not channels or any(channel.exterior_transducer is None for channel in channels):
            raise ValueError("Every channel must carry an exterior transducer in this mode")
        mesh = read_surface(msh_text)
        edges = defaultdict(list)
        for index, triangle in enumerate(mesh.faces):
            for a, b in zip(triangle, np.roll(triangle, -1)):
                edges[tuple(sorted((int(a), int(b))))].append((index, int(a), int(b)))
        if any(
            len(owners) != 2 or owners[0][1:] != owners[1][1:][::-1] for owners in edges.values()
        ):
            raise ValueError("Exterior transducers require closed, consistently oriented solids")
        adjacent = defaultdict(set)
        for owners in edges.values():
            a, b = owners[0][0], owners[1][0]
            adjacent[a].add(b)
            adjacent[b].add(a)
        remaining = set(range(len(mesh.faces)))
        while remaining:
            pending, shell = [remaining.pop()], []
            while pending:
                face = pending.pop()
                shell.append(face)
                neighbours = adjacent[face] & remaining
                remaining.difference_update(neighbours)
                pending.extend(neighbours)
            vertices = mesh.points_m[mesh.faces[shell]]
            # Shift locally before computing volume to avoid translation loss.
            vertices = vertices - vertices.reshape(-1, 3).mean(axis=0)
            volume = (
                np.einsum(
                    "ij,ij->i", vertices[:, 0], np.cross(vertices[:, 1], vertices[:, 2])
                ).sum()
                / 6
            )
            if not np.isfinite(volume) or volume <= 0:
                raise ValueError(
                    "Every exterior solid must have outward face orientation and positive volume"
                )
        drivers_for(record, channels)
    except (ValueError, KeyError, TypeError) as exc:
        return str(exc)
    return None


def drivers_for(record: Mapping, channels: Sequence) -> list[ExteriorDriver]:
    tags = record.get("source_tags")
    if not isinstance(tags, Mapping):
        raise ValueError("Ingestion record requires source_tags")
    drivers = []
    for channel in channels:
        spec = channel.exterior_transducer
        drivers.append(
            ExteriorDriver(
                channel.id,
                tuple(tags[source] for source in channel.source_ids),
                spec.motion_axis,
                **spec.model_dump(exclude={"version", "motion_axis"}),
            )
        )
    return drivers


def package_sweep(
    sweep: TransducerSweep, compiled, request: Any, record: Mapping, *, started: float
) -> dict:
    """Retain native RMS pressure; use the common display mapper without a LEM."""
    geometry = request.geometry
    context = SolverContext.from_imported_request(request, quadrants=1234, source_motion="axial")
    layout, frequencies = compiled.layout, sweep.frequencies_hz
    config = SimpleNamespace(
        observation=SimpleNamespace(
            distance_m=context.polar_config["distance"],
            origin=context.polar_config["observation_origin"],
            sphere_grid=(
                context.polar_config["spherical_theta_count"],
                context.polar_config["spherical_phi_count"],
            ),
        ),
        frame_override=SimpleNamespace(**imported_anchor_frame(record)),
        native_symmetry_plane=None,
    )
    identity = channel_source_identity(geometry, record)
    channels = {}
    for port_index, channel in enumerate(geometry.drive_channels):
        pressure = np.stack(
            [
                np.stack([row.pressure_rms_pa[plane][port_index] for plane in layout.planes])
                for row in sweep.rows
            ]
        )
        sphere = (
            np.stack([row.pressure_rms_pa["sphere"][port_index] for row in sweep.rows])
            if "sphere" in layout.points_m
            else None
        )
        with np.errstate(divide="ignore"):
            spl = 20 * np.log10(np.abs(pressure) / 20e-6)
        native = SweepResult(
            frequencies,
            pressure,
            spl,
            np.full(len(frequencies), complex(np.nan, np.nan)),
            layout.angles_deg,
            list(layout.planes),
            sphere,
            layout.sphere_theta_deg,
            layout.sphere_phi_deg,
            None,
            None,
            sweep.cancelled,
            sweep.requested_frequency_count,
            [],
        )
        response = build_solver_response(
            result=native,
            config=config,
            context=context,
            start_time=started,
            metadata={
                "solver_backend": f"beat-{compiled.wire['solver_options']['bem_backend']}",
                "drive_channel_id": channel.id,
                "source_ids": list(channel.source_ids),
                "phase_time_convention": PHASE_TIME_CONVENTION,
                **identity[channel.id],
            },
            sound_speed_m_per_s=343.0,
        )
        port = compiled.wire["excitation_port_ids"][port_index]
        zin = [row.input_impedance_ohm(port) for row in sweep.rows]
        impedance = {
            "frequencies": frequencies.tolist(),
            "real": [None if z is None else float(z.real) for z in zin],
            "imaginary": [None if z is None else float(solver_to_engineering(z).imag) for z in zin],
        }
        driver_index = sweep.rows[0].transducer_ids.index(
            f"transducer:{quote(channel.id, safe='')}"
        )
        excursion = [float(row.excursion_peak_mm[port_index, driver_index]) for row in sweep.rows]
        response["impedance"] = impedance
        response["metadata"].update(
            impedance_units="ohms",
            impedance_quantity="electrical_input_impedance",
            impedance_drive="rms_voltage",
            impedance_phase_convention=ENGINEERING_PHASE_CONVENTION,
            source_normalization="native_rms_voltage",
            drive={"voltage_v": geometry.drive_voltage_v, "rg_ohm": 0.0},
            driver={
                "label": channel.id,
                "drive_voltage_v": geometry.drive_voltage_v,
                "rg_ohm": 0.0,
                "electrical_impedance_ohm": impedance,
                "cone_excursion_mm": {
                    "frequencies": frequencies.tolist(),
                    "values": excursion,
                    "peak_mm": max(excursion, default=0.0),
                    "quantity": "one_way_peak_displacement",
                },
                "warnings": [],
                "mmd_correction_g": 0.0,
            },
            beat_transducer={
                "version": 1,
                "excitation_port_id": port,
                "transducer_ids": list(sweep.rows[0].transducer_ids),
                "phasor_convention": SOLVER_TIME_CONVENTION,
                "amplitude_convention": "rms",
                "undriven_transducers": "shorted",
                "velocity_rms_m_per_s": [
                    json_safe_native_value(row.velocity_rms_m_per_s[port_index])
                    for row in sweep.rows
                ],
                "current_rms_a": [
                    json_safe_native_value(row.current_rms_a[port_index]) for row in sweep.rows
                ],
                "excursion_peak_mm": [
                    row.excursion_peak_mm[port_index].tolist() for row in sweep.rows
                ],
            },
        )
        channels[channel.id] = response
    return json_safe_native_value(
        {
            "result_kind": "multi_channel",
            "result_contract_version": 2,
            "channel_order": [channel.id for channel in geometry.drive_channels],
            "channels": channels,
            "frequencies": frequencies.tolist(),
            "metadata": {
                "beat_transducer_contract": 1,
                "cancelled": sweep.cancelled,
                "requested_frequency_count": sweep.requested_frequency_count,
                "native_diagnostics": [row.diagnostics for row in sweep.rows],
                "channel_bases_unavailable_reason": "native_voltage_bases_not_supported_by_legacy_recombine",
                "radiation_impedance_matrix": {
                    "frequencies": frequencies.tolist(),
                    "units": "N*s/m",
                    "values": [json_safe_native_value(row.radiation.values) for row in sweep.rows],
                    "metadata": [row.radiation.metadata for row in sweep.rows],
                },
            },
            "_field_trace_unavailable_reason": "native_transducer_traces_not_retained",
        }
    )


def solve(
    msh_text: str,
    request: Any,
    record: Mapping,
    *,
    backend: str,
    stage_callback=None,
    cancellation_callback=None,
    result_callback=None,
    worker_manager=None,
    julia_executable=None,
) -> dict:
    from .official_beat import solve_transducer_compiled

    if getattr(request.geometry, "type", None) != "imported":
        raise ValueError("Exterior transducer jobs require imported geometry")
    # Revalidate even internal/model_construct callers before entering the runtime.
    request = type(request).model_validate(request.model_dump(mode="python"))
    refusal = preflight(record, msh_text, request.geometry.drive_channels, backend=backend)
    if refusal:
        raise ValueError(refusal)
    context = SolverContext.from_imported_request(request, quadrants=1234, source_motion="axial")
    precision = "float64" if backend == "cpu" else "float32"
    frame = imported_anchor_frame(record)
    compiled = build_transducer_request(
        msh_text,
        drivers=drivers_for(record, request.geometry.drive_channels),
        layout=_layout(context, frame, precision),
        frame=frame,
        frequencies_hz=canonical_frequencies(context),
        reference_voltage_rms_v=request.geometry.drive_voltage_v,
        backend=backend,
        precision=precision,
    )
    started = time.time()

    def acquired(sweep):
        if stage_callback:
            stage_callback(
                "solve",
                len(sweep.rows) / sweep.requested_frequency_count,
                f"Solved {len(sweep.rows)} of {sweep.requested_frequency_count} frequencies",
            )
        if result_callback:
            delta = TransducerSweep(sweep.rows[-1:], False, sweep.requested_frequency_count)
            frame = package_sweep(delta, compiled, request, record, started=started)
            frame.pop("_field_trace_unavailable_reason", None)
            frame["metadata"]["provisional"] = {
                "completed_frequency_count": len(sweep.rows),
                "expected_frequency_count": sweep.requested_frequency_count,
            }
            # The runtime appends pressure/impedance deltas. Coupled trace
            # series and their diagnostics are supplied in the durable final.
            frame["metadata"].pop("radiation_impedance_matrix", None)
            for channel in frame["channels"].values():
                channel["metadata"].pop("driver", None)
                channel["metadata"].pop("beat_transducer", None)
            result_callback(len(sweep.rows) - 1, frame)

    native = solve_transducer_compiled(
        compiled,
        worker_manager=worker_manager,
        julia_executable=julia_executable,
        cancellation_callback=cancellation_callback,
        on_frequency_result=acquired,
        status_callback=(lambda message: stage_callback("solve", 0.0, message))
        if stage_callback
        else None,
    )
    return package_sweep(native, compiled, request, record, started=started)
