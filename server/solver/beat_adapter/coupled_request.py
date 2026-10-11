"""Contract-2 requests for verified CAD FEM regions and their closed BEM skin."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.mesh.artifact import read_verified_coupled_exterior_mesh, read_verified_fem_region_mesh
from server.mesh.fem_coupling import read_coupled_exterior
from server.mesh.fem_regions import read_fem_region_domain
from server.solver.coupled_system import ExteriorSurface, FemRegionInput, build_coupled_system
from server.solver.imported import imported_anchor_frame, imported_domain_planes

from .observations import ObservationLayout
from .request import _layout


@dataclass(frozen=True)
class CoupledRequest:
    wire: dict[str, Any]
    layout: ObservationLayout
    frame: Mapping[str, Any]
    regions: tuple[FemRegionInput, ...]
    channel_ports: Mapping[str, tuple[str, ...]]


def build_coupled_request(
    exterior: ExteriorSurface | None, regions: Sequence[FemRegionInput], *,
    layout: ObservationLayout, frequencies_hz: Sequence[float], frame: Mapping[str, Any],
    backend: str = "cpu", precision: str = "float64", symmetry: str = "full",
    ground: Any = None,
) -> CoupledRequest:
    if backend != "cpu":
        raise ValueError("FEM regions require the official BEAT CPU backend")
    if precision != "float64":
        raise ValueError("FEM regions require float64 precision")
    if symmetry not in {"full", "off"} or ground is not None:
        raise ValueError("FEM regions require a full domain without a ground plane")
    if not regions:
        raise ValueError("A coupled request requires declared FEM regions")
    if any(isinstance(f, (bool, np.bool_)) for f in frequencies_hz):
        raise ValueError("Frequencies must be real numbers")
    frequencies = np.asarray(frequencies_hz, dtype=float)
    if (frequencies.ndim != 1 or not frequencies.size or not np.isfinite(frequencies).all()
            or np.any(frequencies <= 0) or len(np.unique(frequencies)) != len(frequencies)):
        raise ValueError("Frequencies must be positive, finite and distinct")
    system = build_coupled_system(exterior, regions)
    channels = {port["name"]: (port["id"],) for port in system["excitation_ports"]}
    outputs = layout.exterior_outputs() if exterior is not None else []
    outputs.append({"id": "fem:pressure", "quantity": "fem_nodal_pressure",
                    "target_ids": [], "options": {}})
    wire = {"schema_version": 1, "compiled_system": system,
            "frequencies_hz": frequencies.tolist(),
            "excitation_port_ids": [p["id"] for p in system["excitation_ports"]],
            "outputs": outputs,
            "solver_options": {"precision": precision, "bem_backend": backend, "symmetry": "off",
                               "phasor_convention": SOLVER_TIME_CONVENTION}}
    return CoupledRequest(wire, layout, frame, tuple(regions), channels)


def build_imported_coupled_request(record: Mapping, geometry: Any, context: Any, *,
                                   frequencies_hz: Sequence[float]) -> CoupledRequest:
    if imported_domain_planes(record) or bool(record.get("infinite_baffle")):
        raise ValueError("FEM regions require a full free-space domain")
    regions = tuple(FemRegionInput(
        region, read_fem_region_domain(read_verified_fem_region_mesh(record, region["region_id"]),
                                      region["region_id"])) for region in record.get("fem_regions", ()))
    has_interfaces = any(g["kind"] == "interface" for r in regions
                         for g in r.record.get("surface_groups", ()))
    exterior = None
    if has_interfaces:
        points, groups = read_coupled_exterior(read_verified_coupled_exterior_mesh(record))
        kinds = {name: dict(value) for name, value in record["coupled_exterior"]["physical_names"].items()}
        # The setup owns painted-source channel assignment; FEM declarations own diaphragms.
        source_channels = {source: channel.id for channel in geometry.drive_channels
                           for source in channel.source_ids}
        for name, kind in kinds.items():
            source = next((part.split("=", 1)[1] for part in name.split("|")
                           if part.startswith("source_id=")), None)
            if source in source_channels:
                kind["drive_channel_id"] = source_channels[source]
            elif source is not None:
                kind["kind"] = "rigid"
                kind.pop("drive_channel_id", None)
        exterior = ExteriorSurface(points, groups, kinds)
    elif geometry.drive_channels:
        from server.mesh.artifact import read_verified_import_mesh
        from .mesh import read_surface

        mesh = read_surface(read_verified_import_mesh(record))
        groups, kinds = {}, {}
        by_source = {s: c.id for c in geometry.drive_channels for s in c.source_ids}
        by_tag = {int(record["source_tags"][s]): c for s, c in by_source.items()}
        for tag in np.unique(mesh.tags):
            name = f"exterior:{tag}"
            groups[name] = mesh.faces[mesh.tags == tag]
            kinds[name] = ({"kind": "moving", "drive_channel_id": by_tag[int(tag)]}
                           if int(tag) in by_tag else {"kind": "rigid"})
        exterior = ExteriorSurface(mesh.points_m * 1000, groups, kinds)
    frame = imported_anchor_frame(record)
    request = build_coupled_request(exterior, regions, layout=_layout(context, frame, "float64"),
                                    frame=frame, frequencies_hz=frequencies_hz)
    missing = {c.id for c in geometry.drive_channels} - set(request.channel_ports)
    if missing:
        raise ValueError(f"Coupled exterior has no moving surface for channels {sorted(missing)}")
    return request
