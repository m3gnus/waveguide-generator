"""Explicit voltage-driven CAD jobs on the official BEAT v3 contract.

Legacy CAD/Boundary Lab paths never enter this module. The first adoption is
outward solids, including x/xy symmetry, bare drivers and independently driven RMS
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
from .beat_adapter.request import _axis, _layout
from .beat_adapter.results import SweepResult
from .beat_adapter.transducers import (
    EXTERIOR_AXIS_SYMMETRY_MESSAGE, ExteriorDriver, TransducerSweep, build_transducer_request, resolve_driver_sd,
)

from .context import SolverContext
from .frequency_sweep import canonical_frequencies
from .imported import imported_anchor_frame, imported_domain_planes, imported_symmetry_from_cut_planes
from .imported_channels import channel_source_identity
from .result_mapping import build_solver_response, json_safe_native_value


def enabled(channels: Sequence) -> bool:
    return any(getattr(channel, "exterior_transducer", None) is not None for channel in channels)


def preflight(record: Mapping, msh_text: str, channels: Sequence, *, backend: str) -> str | None:
    try:
        if (backend not in {"cpu", "metal"}):
            raise ValueError("Exterior transducers require the official BEAT CPU or Metal runtime")
        planes = imported_domain_planes(record)
        if any(p not in {"x0", "y0"} for p in planes):
            raise ValueError("Exterior transducers support only x0 and y0 symmetry planes")
        if not channels or any(channel.exterior_transducer is None for channel in channels):
            raise ValueError("Every channel must carry an exterior transducer in this mode")
        mesh = read_surface(msh_text)
        from server.solver.component_symmetry import infer_component_symmetry
        from server.solver.coupled_admission import validate_driver_sd_geometry

        axes = tuple({"x0": 0, "y0": 1}[p] for p in planes)
        for driver in (() if planes else drivers_for(record, channels)):
            faces = mesh.faces[np.isin(mesh.tags, driver.tags)]
            validate_driver_sd_geometry(
                {"sd_m2": driver.sd_m2, "lumped_sealed_rear_chamber": driver.lumped_sealed_rear_chamber},
                {"exterior": [(faces, mesh.points_m[faces])]}, driver.axis, driver.id)
            infer_component_symmetry({"exterior": [(faces, mesh.points_m[faces])]}, driver.axis,
                                     axes, channel_id=driver.id)
        if planes:
            from server.mesh.surface_topology import orient_closed_surface

            axes = tuple({"x0": 0, "y0": 1}[p] for p in planes)
            tolerance = max(1e-9, float(np.linalg.norm(np.ptp(mesh.points_m, axis=0))) * 1e-6)
            points = mesh.points_m.copy()
            for axis in axes:
                if points[:, axis].min() < -tolerance:
                    raise ValueError("Mesh is outside the positive symmetry fundamental domain")
                points[np.abs(points[:, axis]) <= tolerance, axis] = 0.
            oriented = orient_closed_surface(points * 1000, mesh.faces, into_body=False,
                                             symmetry_plane_axes=axes)
            if not np.array_equal(oriented, mesh.faces):
                raise ValueError("Exterior transducers require consistently outward face orientation")
            for driver in drivers_for(record, channels):
                faces = mesh.faces[np.isin(mesh.tags, driver.tags)]
                moving = {"exterior": [(faces, points[faces])]}
                validate_driver_sd_geometry(
                    {"sd_m2": driver.sd_m2, "lumped_sealed_rear_chamber": driver.lumped_sealed_rear_chamber},
                    moving, driver.axis, driver.id)
                inferred = infer_component_symmetry(moving, driver.axis, axes, channel_id=driver.id)
                resolve_driver_sd({"sd_m2": driver.sd_m2,
                                   "lumped_sealed_rear_chamber": driver.lumped_sealed_rear_chamber},
                                  moving, driver.axis, driver.id,
                                  surface_completion_factor=inferred["surface_completion_factor"])
                if any(abs(_axis(driver.axis)[a]) > 1e-8 for a in axes):
                    raise ValueError(f"Channel {driver.id!r}: {EXTERIOR_AXIS_SYMMETRY_MESSAGE}")
            return None
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
        for driver in drivers_for(record, channels):
            faces = mesh.faces[np.isin(mesh.tags, driver.tags)]
            resolve_driver_sd({"sd_m2": driver.sd_m2,
                               "lumped_sealed_rear_chamber": driver.lumped_sealed_rear_chamber},
                              {"exterior": [(faces, mesh.points_m[faces])]}, driver.axis, driver.id)
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
                **spec.model_dump(exclude={"version", "motion_axis", "power_w", "xmax_mm"}),
            )
        )
    return drivers


def package_sweep(
    sweep: TransducerSweep, compiled, request: Any, record: Mapping, *, started: float
) -> dict:
    """Retain native RMS pressure; use the common display mapper without a LEM."""
    geometry = request.geometry
    symmetry = imported_symmetry_from_cut_planes(imported_domain_planes(record))
    context = SolverContext.from_imported_request(request, quadrants=symmetry.quadrants, source_motion="axial")
    layout, frequencies = compiled.layout, sweep.frequencies_hz
    from .official_beat import engine_version

    solver_engine = {
        "engine": f"beat-{compiled.wire['solver_options']['bem_backend']}",
        "provider": "official",
        "package": "beat-engine",
        "package_version": engine_version(),
        "device": compiled.wire["solver_options"]["bem_backend"],
    }
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
        native_symmetry_plane=symmetry.native_plane,
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
                "solver_engine": solver_engine,
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
        sd = compiled.driver_sd[channel.id]
        response["metadata"].update(sd)
        response["metadata"]["warnings"] = compiled.channel_warnings[channel.id]
        area = sd["sd_m2"]
        if area is not None:
            matrix_id = sweep.rows[0].radiation.component_ids.index(sweep.rows[0].transducer_ids[driver_index])
            medium = compiled.wire["compiled_system"]["regions"][0]
            loads = np.asarray([row.radiation.values[matrix_id, matrix_id] for row in sweep.rows])
            normalized = solver_to_engineering(loads) / (medium["density_kg_per_m3"] * medium["sound_speed_m_per_s"] * area)
            response["acoustic_impedance"] = {
                "frequencies": frequencies.tolist(), "real": normalized.real.tolist(),
                "imaginary": normalized.imag.tolist()}
            response["metadata"]["acoustic_impedance"] = {
                "units": "Z/(rho*c*Sd)", "quantity": "normalized_acoustic_impedance",
                "projected_area_m2": area, "sd_source": sd["sd_source"],
                "phase_convention": ENGINEERING_PHASE_CONVENTION}
        else:
            response.pop("acoustic_impedance", None)
            response["metadata"]["acoustic_impedance_note"] = "Sd not determined; enter Sd to get acoustic impedance"
        response["impedance"] = impedance
        response["metadata"].update(
            impedance_units="ohms",
            impedance_quantity="electrical_input_impedance",
            impedance_drive="rms_voltage",
            impedance_phase_convention=ENGINEERING_PHASE_CONVENTION,
            source_normalization="native_rms_voltage",
            drive={"voltage_v": geometry.drive_voltage_v, "rg_ohm": 0.0},
            driver={
                **({"spec": channel.exterior_transducer.model_dump(mode="json")}
                   if channel.exterior_transducer.version == 2 else {}),
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
                # Explicit Mmd needs no correction, so zero would be valid.
                # Native v3 does not report these diagnostics; leave them absent.
            },
            beat_transducer={
                "version": 1,
                "excitation_port_id": port,
                "transducer_ids": list(sweep.rows[0].transducer_ids),
                "phasor_convention": SOLVER_TIME_CONVENTION,
                "amplitude_convention": "rms",
                "undriven_transducers": "shorted",
                "surface_completion_factors": list(sweep.rows[0].radiation.metadata["surface_completion_factors"]),
                "physical_driver_orbit_counts": list(sweep.rows[0].radiation.metadata["physical_driver_orbit_counts"]),
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
                "solver_engine": solver_engine,
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
    symmetry = imported_symmetry_from_cut_planes(imported_domain_planes(record))
    context = SolverContext.from_imported_request(request, quadrants=symmetry.quadrants, source_motion="axial")
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
        symmetry=symmetry.native_plane or "full",
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
