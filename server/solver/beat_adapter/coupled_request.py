"""Contract-2 requests for verified CAD FEM regions and their closed BEM skin."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.mesh.artifact import read_verified_coupled_exterior_mesh, read_verified_fem_region_mesh
from server.mesh.fem_coupling import read_coupled_exterior
from server.mesh.fem_regions import read_fem_region_domain
from server.solver.coupled_system import ExteriorSurface, FemRegionInput, build_coupled_system
from server.solver.imported import imported_anchor_frame, imported_domain_planes, imported_symmetry_from_cut_planes

from .observations import ObservationLayout
from .request import QUADRATURE_ORDER, REGULAR_QUADRATURE_MODE, SINGULAR_ORDER, _layout, _image_frame


@dataclass(frozen=True)
class CoupledRequest:
    wire: dict[str, Any]
    layout: ObservationLayout
    frame: Mapping[str, Any]
    regions: tuple[FemRegionInput, ...]
    channel_ports: Mapping[str, tuple[str, ...]]

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self.wire["compiled_system"]["metadata"].get("warnings", ()))

    @property
    def drive_normalization(self) -> dict[str, str]:
        kinds = {p["id"]: p["kind"] for p in self.wire["compiled_system"]["excitation_ports"]}
        return {channel: "voltage_rms" if kinds[ports[0]] == "voltage" else "unit_acceleration"
                for channel, ports in self.channel_ports.items()}


def validate_coupled_execution(backend: str, precision: str, *, interior: bool) -> None:
    if backend not in {"cpu", "metal", "cuda", "rocm"}:
        raise ValueError("FEM regions require official BEAT CPU, Metal, CUDA or ROCm")
    if precision != ("float64" if backend == "cpu" else "float32"):
        raise ValueError("Coupled FEM requires CPU float64 or GPU float32")
    if interior and (backend, precision) != ("cpu", "float64"):
        raise ValueError("Interior-only FEM requires CPU float64")


def build_coupled_request(
    exterior: ExteriorSurface | None, regions: Sequence[FemRegionInput], *,
    layout: ObservationLayout, frequencies_hz: Sequence[float], frame: Mapping[str, Any],
    backend: str = "cpu", precision: str = "float64", symmetry: str = "full",
    ground: Any = None, transducers: Mapping[str, Any] | None = None,
    reference_voltage_rms_v: float = 2.83,
) -> CoupledRequest:
    requested_backend, requested_precision = backend, precision
    if backend not in {"cpu", "metal", "cuda", "rocm"} or precision not in {"float32", "float64"}:
        raise ValueError("Unsupported FEM backend or precision")
    if exterior is None:
        backend, precision = "cpu", "float64"
    validate_coupled_execution(backend, precision, interior=exterior is None)
    mode, rotation, translation = _image_frame(symmetry, None)
    axes = (0,) if mode == "x" else (0, 1) if mode == "xy" else ()
    if not regions:
        raise ValueError("A coupled request requires declared FEM regions")
    frequencies = np.asarray(frequencies_hz, dtype=float)
    if frequencies.ndim != 1:
        raise ValueError("Frequencies must be positive, finite and distinct")
    if any(isinstance(f, (bool, np.bool_)) for f in frequencies_hz):
        raise ValueError("Frequencies must be real numbers")
    with np.errstate(over="ignore", under="ignore"):
        wire_frequencies = frequencies.astype(precision)
    if (frequencies.ndim != 1 or not frequencies.size or not np.isfinite(frequencies).all()
            or not np.isfinite(wire_frequencies).all() or np.any(wire_frequencies <= 0)
            or len(np.unique(wire_frequencies)) != len(wire_frequencies)
            or np.any(frequencies <= 0) or len(np.unique(frequencies)) != len(frequencies)):
        raise ValueError("Frequencies must be positive, finite and distinct")
    from server.solver.coupled_admission import fem_request_problem

    problem = fem_request_problem(
        {"fem_regions": [r.record for r in regions],
         "symmetry": {"domain_planes": [{0: "x0", 1: "y0"}[axis] for axis in axes]}},
        ground=ground is not None, has_exterior=exterior is not None,
        has_transducers=bool(transducers))
    if problem:
        raise ValueError(problem[1])

    all_points = np.concatenate([item.domain.points_mm for item in regions]
                                + ([exterior.points_mm] if exterior is not None else []))
    all_points = (all_points + translation * 1000) @ rotation.T
    # FEM OCC/gmsh builders declare no larger geometric tolerance. Keep
    # one union tolerance for shared interfaces, capped at 1e-4 mm so a large
    # remote mesh cannot move genuine vertices of a small region.
    tolerance = min(1e-4, max(1e-6, float(np.linalg.norm(np.ptp(all_points, axis=0))) * 1e-6))

    def image_points(points, surfaces, mesh_name, tetrahedra=None):
        points = (np.asarray(points, dtype=float) + translation * 1000) @ rotation.T
        before = points.copy()
        for axis in axes:
            if np.min(points[:, axis]) < -tolerance:
                raise ValueError(f"Mesh {mesh_name!r} is outside the positive symmetry fundamental domain")
            points[np.abs(points[:, axis]) <= tolerance, axis] = 0.
        if axes:
            for group, faces in surfaces.items():
                def normals(vertices):
                    corners = vertices[faces]
                    return np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
                old, new = normals(before), normals(points)
                sizes = np.linalg.norm(new, axis=1)
                collapsed = np.flatnonzero(~np.isfinite(sizes) | (sizes <= np.linalg.norm(old, axis=1) * 1e-12))
                if len(collapsed):
                    raise ValueError(f"Symmetry snapping collapsed a triangle: mesh {mesh_name!r}, "
                                     f"group {group!r}, element {collapsed[0]}")
                flipped = np.flatnonzero(np.einsum("ij,ij->i", old, new) <= 0)
                if len(flipped):
                    raise ValueError(f"Symmetry snapping flipped a triangle: mesh {mesh_name!r}, "
                                     f"group {group!r}, element {flipped[0]}")
            if tetrahedra is not None:
                def determinants(vertices):
                    corners = vertices[tetrahedra]
                    return np.linalg.det(corners[:, 1:] - corners[:, :1])
                old, new = determinants(before), determinants(points)
                collapsed = np.flatnonzero(~np.isfinite(new) | (np.abs(new) <= np.abs(old) * 1e-12))
                if len(collapsed):
                    raise ValueError(f"Symmetry snapping collapsed a tetrahedron: mesh {mesh_name!r}, "
                                     f"element {collapsed[0]}")
                flipped = np.flatnonzero(np.sign(old) != np.sign(new))
                if len(flipped):
                    raise ValueError(f"Symmetry snapping flipped a tetrahedron: mesh {mesh_name!r}, "
                                     f"element {flipped[0]}")
        return points

    transformed_regions = [replace(item, domain=replace(
        item.domain, points_mm=image_points(item.domain.points_mm, item.domain.surfaces,
                                           item.record["region_id"], item.domain.tetrahedra))) for item in regions]
    transformed_exterior = (replace(exterior, points_mm=image_points(exterior.points_mm, exterior.groups, "exterior"))
                            if exterior is not None else None)
    transformed_layout = replace(layout, points_m={
        name: (points + translation) @ rotation.T for name, points in layout.points_m.items()})
    system = build_coupled_system(transformed_exterior, transformed_regions, transducers=transducers,
                                  symmetry_plane_axes=axes)
    channels = {port["name"]: (port["id"],) for port in system["excitation_ports"]}
    outputs = transformed_layout.exterior_outputs() if exterior is not None else []
    if system["interfaces"]:
        # Empty targets select all interfaces. Radiation uses the same transformed
        # observation points as total pressure, including the sampled sphere.
        outputs.extend({**output, "id": output["id"].replace("pressure:", "interface:radiation:"),
                        "quantity": "interface_radiated_pressure"} for output in list(outputs))
        outputs.append({"id": "interface:velocity", "quantity": "interface_average_normal_velocity",
                        "target_ids": [], "options": {}})
    outputs.append({"id": "fem:pressure", "quantity": "fem_nodal_pressure",
                    "target_ids": [], "options": {}})
    if transducers:
        outputs.extend({"id": q, "quantity": q, "target_ids": [], "options": {}}
                       for q in ("diaphragm_velocity", "voice_coil_current"))
    wire = {"schema_version": 1, "compiled_system": system,
            "frequencies_hz": frequencies.tolist(),
            "excitation_port_ids": [p["id"] for p in system["excitation_ports"]],
            "outputs": outputs,
            "solver_options": {"precision": precision, "bem_backend": backend, "symmetry": mode,
                               "phasor_convention": SOLVER_TIME_CONVENTION,
                               "regular_quadrature_mode": REGULAR_QUADRATURE_MODE,
                               "quadrature_order": QUADRATURE_ORDER, "singular_order": SINGULAR_ORDER,
                               # Native validation diagnostics add costly matrices; qualification
                               # requests opt in separately, ordinary jobs retain field checks.
                               "validation_diagnostics": False}}
    if transducers:
        from .transducers import _positive

        wire["solver_options"]["transducer_reference_voltage_v"] = float(np.asarray(
            _positive(reference_voltage_rms_v, "transducer_reference_voltage_v"), dtype=precision))
    if (backend, precision) != (requested_backend, requested_precision):
        system["metadata"]["fem_execution_reroute"] = {
            "requested_backend": requested_backend, "requested_precision": requested_precision,
            "backend": backend, "precision": precision,
            "reason": "Interior-only FEM uses CPU float64"}
    return CoupledRequest(wire, layout, frame, tuple(regions), channels)


def build_imported_coupled_request(record: Mapping, geometry: Any, context: Any, *,
                                   frequencies_hz: Sequence[float],
                                   resolved_quadrants: int | None = None, backend: str = "cpu",
                                   precision: str | None = None) -> CoupledRequest:
    from server.solver.coupled_admission import fem_request_problem

    problem = fem_request_problem(
        record, geometry.drive_channels,
        ground=getattr(context, "ground_plane", None) is not None,
        passive_cardioid=getattr(geometry, "passive_cardioid_enabled", False),
        combine_id=getattr(getattr(geometry, "combine", None), "id", None))
    if problem:
        raise ValueError(f"{problem[0]}: {problem[1]}")
    symmetry = imported_symmetry_from_cut_planes(imported_domain_planes(record))
    if resolved_quadrants is not None and symmetry.quadrants != resolved_quadrants:
        raise ValueError("Stored job and ingestion record disagree on symmetry")
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
    elif any(c.source_ids for c in geometry.drive_channels):
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
    # FEM keeps the CPU float64 reference for every drive; GPUs use float32.
    precision = precision or ("float64" if backend == "cpu" else "float32")
    request = build_coupled_request(exterior, regions, layout=_layout(context, frame, precision),
                                    backend=backend, precision=precision,
                                    frame=frame, frequencies_hz=frequencies_hz,
                                    symmetry=symmetry.native_plane or "full", ground=context.ground_plane,
                                    transducers={c.id: c.exterior_transducer for c in geometry.drive_channels
                                                 if getattr(c, "exterior_transducer", None) is not None},
                                    reference_voltage_rms_v=getattr(geometry, "drive_voltage_v", 2.83))
    missing = {c.id for c in geometry.drive_channels} - set(request.channel_ports)
    if missing:
        raise ValueError(f"Coupled exterior has no moving surface for channels {sorted(missing)}")
    if getattr(geometry, "combine", None) is not None and geometry.combine.id in request.channel_ports:
        raise ValueError(f"Combine id {geometry.combine.id!r} collides with a compiled drive channel id")
    return request
