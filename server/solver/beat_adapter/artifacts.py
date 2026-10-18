"""Lossless coupled field arrays; the job runtime owns optional publication."""
from __future__ import annotations

import base64
import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION


def fem_field_arrays(compiled, sweep, record):
    from server.solver.imported import imported_domain_planes

    arrays = {"schema": np.asarray("wg-fem-field-store-v2"),
              "phasor_convention": np.asarray(compiled.wire["solver_options"]["phasor_convention"]),
              "drive_normalization": np.asarray(list(compiled.drive_normalization.values())),
              "frequencies_hz": next(iter(sweep.channels.values())).frequencies_hz,
              "channel_ids": np.asarray(list(compiled.channel_ports)),
              "region_ids": np.asarray([r.domain.region_id for r in compiled.regions]),
              "pressure": sweep.nodal_pressure,
              "symmetry_planes": np.asarray(imported_domain_planes(record), dtype="U2")}
    voltage = compiled.wire["solver_options"].get("transducer_reference_voltage_v")
    if voltage is not None:
        arrays["transducer_reference_voltage_v"] = np.asarray(voltage)
    for index, region in enumerate(compiled.regions):
        arrays[f"points_mm_{index}"] = region.domain.points_mm
        arrays[f"tetrahedra_{index}"] = region.domain.tetrahedra
        names = list(region.domain.surfaces)
        arrays[f"surface_names_{index}"] = np.asarray(names)
        for j, name in enumerate(names):
            arrays[f"surface_triangles_{index}_{j}"] = region.domain.surfaces[name]
    arrays["density_kg_per_m3"] = np.asarray(compiled.regions[0].record["medium"]["density_kg_per_m3"])
    return arrays


def trace_arrays(request, sweep) -> dict | None:
    if sweep.bem_pressure is None:
        return None
    resource = next(m for m in request.wire["compiled_system"]["meshes"] if m["purpose"] == "bem_surface")
    mesh = resource["mesh_data"]
    def unpack(value):
        return np.frombuffer(base64.b64decode(value["data"]), dtype=value["dtype"]).reshape(value["shape"])
    medium = next(r for r in request.wire["compiled_system"]["regions"] if r["kind"] == "unbounded_air")
    return {"schema": np.asarray("wg-bem-traces-v1"),
            "phasor_convention": np.asarray(SOLVER_TIME_CONVENTION),
            "frequencies_hz": next(iter(sweep.channels.values())).frequencies_hz,
            "channel_ids": np.asarray(list(request.channel_ports)),
            "points_m": unpack(mesh["points"]),
            "triangles": unpack(mesh["cells"][0]["connectivity"]),
            "pressure": sweep.bem_pressure, "neumann": sweep.bem_neumann,
            "sound_speed_m_per_s": np.asarray(medium["sound_speed_m_per_s"]),
            "density_kg_per_m3": np.asarray(medium["density_kg_per_m3"]),
            "image_rotation": np.eye(3) if request.image_rotation is None else request.image_rotation,
            "symmetry": np.asarray(request.wire["solver_options"]["symmetry"])}
