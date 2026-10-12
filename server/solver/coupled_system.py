"""Compile a coupled FEM/BEM system for the official BEAT worker.

The CAD Link exterior is a BEM surface; declared FEM regions
(``fem-regions-v1``) are bounded air meshed with tetrahedra
(:mod:`server.mesh.fem_regions`). This module turns both into BEAT's
``compiled_system`` (contract version 2, ``coupled_fem_bem_lem`` /
``interior_fem`` solve kinds), the same contract Boundary Lab drives:

* one ``unbounded_air`` region on the exterior surface mesh, and one
  ``bounded_air`` region per FEM region, each with its own medium;
* a boundary per physical surface group: ``rigid``, ``moving``,
  ``interface`` or ``plane_wave_tube_termination``;
* one ``ideal_velocity_source`` component per drive channel, owning every
  moving boundary that channel drives -- an exterior source patch and a FEM
  diaphragm on the same channel move together, as one cone seen from both
  sides -- with a ``normal_velocity`` excitation port; configured drivers use
  one ``electrodynamic_transducer`` and ``voltage`` port instead, sharing the
  mechanical coordinate across every FEM and BEM moving boundary;
* one interface per FEM ``interface`` group, paired with the exterior patch
  of the same physical name, with its conforming topology map.

Meshes travel inline as BEAT ``mesh_data`` (points in metres after
``scale_to_m``, one triangle block and, for a FEM region, one tetrahedron
block), so a face index in a topology map is simply a row of that mesh's
triangle block.

Nothing here runs a solve: :func:`build_coupled_system` produces the dictionary
and ``beat_engine.beat_contract.validate_compiled_system`` is its verdict.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from server.mesh.fem_regions import FemRegionDomain, parse_physical_name
from server.mesh.surface_topology import open_edges, orient_closed_surface

CONTRACT_VERSION = 2
MM_TO_M = 1e-3
EXTERIOR_REGION_ID = "region:exterior-air"
EXTERIOR_MESH_ID = "mesh:exterior"
# Interface vertices are the same points written twice; anything beyond this
# (in millimetres) is a mesh that does not conform, not round-off.
INTERFACE_TOLERANCE_MM = 1e-6
# BEM normals point out of the radiating body, into the exterior air. Measured
# against BEAT on a box open on one face (2026-10-11): this winding gives the
# duct pressure (tens of pascals at 100 Hz) and the monopole far field, while
# the opposite winding gives kilopascals inside -- and BEAT's own interface
# flux and continuity diagnostics read clean either way, so only the winding
# here prevents it.
EXTERIOR_NORMALS_INTO_BODY = False

_BOUNDARY_KINDS = {
    "rigid": "rigid",
    "moving": "moving",
    "interface": "interface",
    "plane-wave-termination": "plane_wave_tube_termination",
}


class CoupledSystemError(ValueError):
    """The meshes cannot form a valid coupled system; the message says why."""


@dataclass(frozen=True)
class ExteriorSurface:
    """The exterior BEM surface in millimetres, triangles grouped by name.

    ``groups`` maps a physical name to its triangles, and ``kinds`` gives each
    name's boundary kind (``rigid``, ``moving`` or ``interface``) and, for a
    moving patch, its drive channel. An interface patch carries the same
    physical name as the FEM group it couples to.
    """

    points_mm: np.ndarray
    groups: Mapping[str, np.ndarray]
    kinds: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class FemRegionInput:
    record: Mapping[str, Any]  # the validated scope.fem_regions[] entry
    domain: FemRegionDomain


def _packed(array: np.ndarray, dtype: str) -> dict[str, Any]:
    data = np.ascontiguousarray(array, dtype=np.dtype(dtype))
    return {
        "dtype": dtype,
        "shape": [int(value) for value in data.shape],
        "data": base64.b64encode(data.tobytes()).decode("ascii"),
    }


def _ordered(groups: Mapping[str, np.ndarray]) -> list[tuple[str, np.ndarray]]:
    return sorted(groups.items())


def _oriented_exterior_surfaces(points_mm, groups, *, symmetry_plane_axes=()):
    """Use the same closed-shell winding for admission Sd and compilation."""
    surfaces = _ordered(groups)
    triangles = orient_closed_surface(
        np.asarray(points_mm, dtype=np.float64),
        np.concatenate([block for _name, block in surfaces]),
        into_body=EXTERIOR_NORMALS_INTO_BODY, symmetry_plane_axes=symmetry_plane_axes)
    result, offset = [], 0
    for name, block in surfaces:
        result.append((name, triangles[offset:offset + len(block)]))
        offset += len(block)
    return result


def _mesh_data(
    points_mm: np.ndarray,
    surfaces: list[tuple[str, np.ndarray]],
    *,
    tetrahedra: np.ndarray | None = None,
    volume_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, tuple[int, int]]]:
    """Pack one mesh: every surface in one triangle block, in ``surfaces`` order."""

    names: dict[str, tuple[int, int]] = {}
    triangles, tags = [], []
    for index, (name, block) in enumerate(surfaces, start=1):
        names[name] = (index, 2)
        triangles.append(np.asarray(block, dtype=np.int64))
        tags.append(np.full(len(block), index, dtype=np.int64))
    cells = [{
        "type": "triangle",
        "connectivity": _packed(np.concatenate(triangles), "<i8"),
        "physical_tags": _packed(np.concatenate(tags), "<i8"),
    }]
    if tetrahedra is not None:
        tag = len(surfaces) + 1
        names[str(volume_name)] = (tag, 3)
        cells.append({
            "type": "tetra",
            "connectivity": _packed(tetrahedra, "<i8"),
            "physical_tags": _packed(np.full(len(tetrahedra), tag, dtype=np.int64), "<i8"),
        })
    data = {
        "schema_version": 1,
        "points": _packed(np.asarray(points_mm, dtype=np.float64) * MM_TO_M, "<f8"),
        "cells": cells,
        "physical_names": {name: [tag, dim] for name, (tag, dim) in names.items()},
    }
    return data, names


def _normals(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    corners = points[triangles]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths <= 0):
        raise CoupledSystemError("An interface contains a degenerate triangle.")
    return normals / lengths[:, None]


def _boundary_faces(tetrahedra: np.ndarray) -> set[tuple[int, int, int]]:
    faces = np.concatenate([tetrahedra[:, [1, 2, 3]], tetrahedra[:, [0, 2, 3]],
                            tetrahedra[:, [0, 1, 3]], tetrahedra[:, [0, 1, 2]]])
    keys, counts = np.unique(np.sort(faces, axis=1), axis=0, return_counts=True)
    return {tuple(int(value) for value in row) for row in keys[counts == 1]}



def _fem_driver_triangles(domain: FemRegionDomain) -> dict[tuple[int, int, int], np.ndarray]:
    """Driver-side winding from tetrahedra, matching the engine's FEM normals.

    A bounded region may have inner walls. Derive orientation from each
    facet's opposite tetrahedral vertex instead of imposing BEM skin rules.
    Reverse the air-domain outward winding for projected driver area only.
    """
    tets = domain.tetrahedra
    faces = np.concatenate([tets[:, [1, 2, 3]], tets[:, [0, 2, 3]],
                            tets[:, [0, 1, 3]], tets[:, [0, 1, 2]]])
    opposite = np.concatenate([tets[:, i] for i in range(4)])
    keys, indices, counts = np.unique(np.sort(faces, axis=1), axis=0,
                                      return_index=True, return_counts=True)
    result = {}
    for key, index in zip(keys[counts == 1], indices[counts == 1]):
        face = faces[index].copy()
        corners = np.asarray(domain.points_mm[face], dtype=np.float64)
        normal = np.cross(corners[1] - corners[0], corners[2] - corners[0])
        # Driver normal points into the adjacent air tetrahedron.
        if np.dot(normal, domain.points_mm[opposite[index]] - corners[0]) < 0:
            face[[1, 2]] = face[[2, 1]]
        result[tuple(int(v) for v in key)] = np.asarray(domain.points_mm[face], dtype=np.float64) * MM_TO_M
    return result


def interface_topology(
    fem_points_mm: np.ndarray,
    fem_tetrahedra: np.ndarray,
    fem_triangles: np.ndarray,
    fem_rows: np.ndarray,
    bem_points_mm: np.ndarray,
    bem_triangles: np.ndarray,
    bem_rows: np.ndarray,
    *,
    tolerance_mm: float = INTERFACE_TOLERANCE_MM,
) -> dict[str, Any]:
    """The conforming FEM-to-BEM map for one interface, in BEAT's terms.

    ``fem_rows`` and ``bem_rows`` are the interface's rows in each mesh's
    triangle block. Every FEM interface vertex must coincide with exactly one
    BEM interface vertex, the triangles must match one to one, and every FEM
    interface triangle must be a facet on the boundary of the tetrahedra.
    ``normal_sign`` is +1 where the two windings agree.
    """

    from scipy.spatial import cKDTree

    fem_faces = fem_triangles[fem_rows]
    bem_faces = bem_triangles[bem_rows]
    if len(fem_faces) != len(bem_faces):
        raise CoupledSystemError(
            f"Interface triangle counts differ: FEM {len(fem_faces)}, BEM {len(bem_faces)}."
        )
    fem_vertices = np.unique(fem_faces)
    bem_vertices = np.unique(bem_faces)
    if len(fem_vertices) != len(bem_vertices):
        raise CoupledSystemError(
            f"Interface vertex counts differ: FEM {len(fem_vertices)}, BEM {len(bem_vertices)}."
        )
    distances, nearest = cKDTree(bem_points_mm[bem_vertices]).query(fem_points_mm[fem_vertices])
    error = float(np.max(distances, initial=0.0))
    if error > tolerance_mm:
        raise CoupledSystemError(
            f"The exterior does not conform to the FEM interface: vertices differ by up to {error:g} mm."
        )
    mapped = bem_vertices[nearest]
    if len(np.unique(mapped)) != len(mapped):
        raise CoupledSystemError("FEM interface vertices do not map one to one onto the exterior.")
    to_bem = dict(zip(fem_vertices.tolist(), mapped.tolist()))
    by_key = {tuple(sorted(face.tolist())): int(row) for face, row in zip(bem_faces, bem_rows)}
    fem_normals = _normals(fem_points_mm, fem_faces)
    bem_face_indices, signs = [], []
    for face, normal in zip(fem_faces, fem_normals):
        row = by_key.get(tuple(sorted(to_bem[int(vertex)] for vertex in face)))
        if row is None:
            raise CoupledSystemError("FEM and exterior interface triangles do not match one to one.")
        bem_face_indices.append(row)
        bem_normal = _normals(bem_points_mm, bem_triangles[[row]])[0]
        signs.append(1 if float(np.dot(normal, bem_normal)) >= 0 else -1)
    facets = _boundary_faces(fem_tetrahedra)
    on_boundary = sum(tuple(sorted(face.tolist())) in facets for face in fem_faces)
    if on_boundary != len(fem_faces):
        raise CoupledSystemError(
            f"Only {on_boundary} of {len(fem_faces)} FEM interface triangles bound the tetrahedra."
        )
    return {
        "fem_vertex_indices": fem_vertices.tolist(),
        "fem_to_bem_vertex_indices": [to_bem[int(vertex)] for vertex in fem_vertices],
        "fem_face_indices": [int(row) for row in fem_rows],
        "bem_face_indices": bem_face_indices,
        "normal_sign": signs,
        "max_coordinate_error": error * MM_TO_M,
        "fem_facets_on_tetra_boundary": int(on_boundary),
        "bem_boundary_edges": open_edges(bem_triangles),
    }


def compact_surface(surface: ExteriorSurface) -> ExteriorSurface:
    """Drop vertices no triangle references, renumbering the rest in order."""

    used = np.unique(np.concatenate([np.asarray(block).ravel() for block in surface.groups.values()]))
    if len(used) == len(surface.points_mm):
        return surface
    remap = -np.ones(len(surface.points_mm), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return ExteriorSurface(
        points_mm=np.asarray(surface.points_mm)[used],
        groups={name: remap[np.asarray(block)] for name, block in surface.groups.items()},
        kinds=surface.kinds,
    )


def _rows(surfaces: list[tuple[str, np.ndarray]]) -> dict[str, np.ndarray]:
    rows, start = {}, 0
    for name, block in surfaces:
        rows[name] = np.arange(start, start + len(block), dtype=np.int64)
        start += len(block)
    return rows


def build_coupled_system(
    exterior: ExteriorSurface | None,
    regions: Sequence[FemRegionInput],
    *,
    transducers: Mapping[str, Any] | None = None,
    system_id: str = "wg-cad-coupled",
    name: str = "CAD Link coupled FEM/BEM system",
    symmetry_plane_axes: tuple[int, ...] = (),
) -> dict[str, Any]:
    """Return BEAT's ``compiled_system`` for the exterior and FEM regions.

    ``exterior`` may be ``None`` for an interior-only solve (a compression
    driver's chambers with a plane-wave termination), in which case no region
    may declare an interface. The exterior must be closed: an interface patch
    is part of its surface, not a hole in it.
    """

    if not regions:
        raise CoupledSystemError("A coupled system needs at least one FEM region.")
    from server.solver.coupled_admission import fem_drive_problem, fem_termination_problem

    problem = fem_termination_problem([r.record for r in regions], has_exterior=exterior is not None)
    if problem:
        raise CoupledSystemError(f"{problem[0]}: {problem[1]}")
    problem = fem_drive_problem([r.record for r in regions], {}, painted_sources=set(),
                               electrodynamic_channels={channel for channel, spec in (transducers or {}).items()
                                                        if spec is not None})
    if problem and problem[0] == "fem_group_options_invalid":
        raise CoupledSystemError(f"{problem[0]}: {problem[1]}")
    if problem and problem[0] in {"fem_multiple_diaphragms_unsupported", "fem_missing_channel"}:
        raise CoupledSystemError(problem[1])
    meshes: list[dict[str, Any]] = []
    region_records: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    interfaces: list[dict[str, Any]] = []
    moving_by_channel: dict[str, list[str]] = {}
    motion_weights: dict[str, float] = {}
    moving_triangle_weights: dict[str, dict[str, list[np.ndarray]]] = {}
    moving_triangles: dict[str, dict[str, list[tuple[np.ndarray, np.ndarray]]]] = {}
    exterior_boundary_by_name: dict[str, str] = {}
    exterior_rows: dict[str, np.ndarray] = {}
    exterior_triangles = np.zeros((0, 3), dtype=np.int64)

    if exterior is not None:
        # Every exterior vertex is a P1 unknown, so a point no triangle uses
        # would leave an empty row: keep only the referenced ones.
        exterior = compact_surface(exterior)
        try:
            surfaces = _oriented_exterior_surfaces(
                exterior.points_mm, exterior.groups, symmetry_plane_axes=symmetry_plane_axes)
        except ValueError as exc:
            raise CoupledSystemError(str(exc)) from exc
        exterior_triangles = np.concatenate([block for _name, block in surfaces])
        edges = open_edges(exterior_triangles)
        if edges and not symmetry_plane_axes:
            raise CoupledSystemError(
                f"The exterior surface has {edges} open edges; close every opening "
                "with an interface patch before coupling."
            )
        data, exterior_tags = _mesh_data(exterior.points_mm, surfaces)
        meshes.append({"id": EXTERIOR_MESH_ID, "name": "Exterior", "file": "",
                       "purpose": "bem_surface", "scale_to_m": 1.0,
                       "translation_m": [0.0, 0.0, 0.0], "mesh_data": data})
        region_records.append({
            "id": EXTERIOR_REGION_ID, "name": "Exterior air", "kind": "unbounded_air",
            "mesh_ids": [EXTERIOR_MESH_ID], "volume_groups": [],
            "sound_speed_m_per_s": 343.0, "density_kg_per_m3": 1.21, "loss_model": {},
        })
        exterior_rows = _rows(surfaces)
        for index, (group_name, block) in enumerate(surfaces):
            kind_record = exterior.kinds.get(group_name) or {}
            kind = kind_record.get("kind", "rigid")
            if "lining" in kind_record or "wall_impedance" in kind_record:
                raise CoupledSystemError(f"Exterior group {group_name!r}: lining is supported only on rigid FEM boundaries.")
            if kind not in {"rigid", "moving", "interface"}:
                raise CoupledSystemError(f"Exterior group {group_name!r} has unknown kind {kind!r}.")
            boundary_id = f"boundary:exterior:{index}"
            exterior_boundary_by_name[group_name] = boundary_id
            boundaries.append({
                "id": boundary_id, "name": group_name, "region_id": EXTERIOR_REGION_ID,
                "kind": kind,
                "group": {"mesh_id": EXTERIOR_MESH_ID, "dimension": 2, "name": group_name,
                          "tag": exterior_tags[group_name][0]},
                "parameters": {},
            })
            if kind == "moving":
                channel = kind_record.get("drive_channel_id")
                if not channel:
                    raise CoupledSystemError(f"Exterior source {group_name!r} names no drive channel.")
                moving_by_channel.setdefault(str(channel), []).append(boundary_id)
                weight = kind_record.get("motion_weight", 1.0)
                if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not np.isfinite(weight) or weight <= 0:
                    raise CoupledSystemError(f"Exterior source {group_name!r}: motion weight must be finite and positive.")
                motion_weights[boundary_id] = float(weight)
                if channel in (transducers or {}):
                    moving_triangle_weights.setdefault(str(channel), {}).setdefault(EXTERIOR_MESH_ID, []).append(np.full(len(block), weight))
                    moving_triangles.setdefault(str(channel), {}).setdefault(EXTERIOR_MESH_ID, []).append(
                        (block, np.asarray(exterior.points_mm[block], dtype=np.float64) * MM_TO_M))

    paired: set[str] = set()
    media = set()
    for item in regions:
        record, domain = item.record, item.domain
        region_id = str(record["region_id"])
        mesh_id = f"mesh:fem:{region_id}"
        region_key = f"region:fem:{region_id}"
        surfaces = _ordered(domain.surfaces)
        volume_name = f"wg-fem-v1|region={region_id}|volume"
        data, tags = _mesh_data(domain.points_mm, surfaces,
                                tetrahedra=domain.tetrahedra, volume_name=volume_name)
        meshes.append({"id": mesh_id, "name": str(record.get("name") or region_id), "file": "",
                       "purpose": "fem_volume", "scale_to_m": 1.0,
                       "translation_m": [0.0, 0.0, 0.0], "mesh_data": data})
        medium = record["medium"]
        media.add((float(medium["sound_speed_m_per_s"]), float(medium["density_kg_per_m3"])))
        region_records.append({
            "id": region_key, "name": str(record.get("name") or region_id), "kind": "bounded_air",
            "mesh_ids": [mesh_id],
            "volume_groups": [{"mesh_id": mesh_id, "dimension": 3, "name": volume_name,
                               "tag": tags[volume_name][0]}],
            "sound_speed_m_per_s": float(medium["sound_speed_m_per_s"]),
            "density_kg_per_m3": float(medium["density_kg_per_m3"]),
            "loss_model": {"bulk_loss_factor": float(medium["bulk_loss_factor"])},
        })
        declarations = {
            str(group["group_id"]): group
            for group in record.get("surface_groups") or ()
        }
        for group_id, declaration in declarations.items():
            if "lining" in declaration or "relative_level_db" in declaration:
                expected = f"wg-fem-v1|region={region_id}|group={group_id}|kind={declaration['kind']}"
                if expected not in domain.surfaces:
                    raise CoupledSystemError(f"FEM region {region_id!r}, group {group_id!r}: "
                                             "boundary physics has no matching mesh group.")
        fem_triangles = np.concatenate([block for _n, block in surfaces]).astype(np.int64)
        rows = _rows(surfaces)
        driver_triangles = None
        for group_name, block in surfaces:
            fields = parse_physical_name(group_name)
            if fields is None or "kind" not in fields:
                raise CoupledSystemError(f"FEM region {region_id!r} carries a foreign surface {group_name!r}.")
            kind = _BOUNDARY_KINDS[fields["kind"]]
            boundary_id = f"boundary:fem:{region_id}:{fields['group']}"
            declaration = declarations.get(fields["group"], {})
            if declaration and declaration["kind"] != fields["kind"]:
                raise CoupledSystemError(f"FEM group {group_name!r} disagrees with its declared kind.")
            boundary_params = {}
            if "lining" in declaration:
                if kind != "rigid":
                    raise CoupledSystemError(f"FEM group {group_name!r}: lining requires a rigid boundary.")
                lining = declaration["lining"]
                boundary_params["wall_impedance"] = {
                    "model": lining["model"], "thickness_m": lining["thickness_mm"] * MM_TO_M,
                    "flow_resistivity_pa_s_per_m2": lining["flow_resistivity_pa_s_per_m2"],
                }
            boundaries.append({
                "id": boundary_id, "name": group_name, "region_id": region_key, "kind": kind,
                "group": {"mesh_id": mesh_id, "dimension": 2, "name": group_name,
                          "tag": tags[group_name][0]},
                "parameters": boundary_params,
            })
            if kind == "moving":
                channel = declaration.get("drive_channel_id")
                if not channel:
                    raise CoupledSystemError(f"Diaphragm {group_name!r} names no drive channel.")
                moving_by_channel.setdefault(str(channel), []).append(boundary_id)
                weight = 10 ** (float(declaration.get("relative_level_db", 0.0)) / 20)
                motion_weights[boundary_id] = weight
                if channel in (transducers or {}):
                    moving_triangle_weights.setdefault(str(channel), {}).setdefault(mesh_id, []).append(np.full(len(block), weight))
                    if driver_triangles is None:
                        driver_triangles = _fem_driver_triangles(domain)
                    try:
                        corners = np.asarray([driver_triangles[tuple(sorted(face))] for face in block])
                    except KeyError as exc:
                        raise CoupledSystemError(f"Diaphragm {group_name!r} is not on the tetrahedral boundary") from exc
                    moving_triangles.setdefault(str(channel), {}).setdefault(mesh_id, []).append((block, corners))
            elif kind == "interface":
                partner = exterior_boundary_by_name.get(group_name)
                if exterior is None or partner is None:
                    raise CoupledSystemError(
                        f"FEM interface {fields['group']!r} of region {region_id!r} has no "
                        "matching exterior patch; the exterior must carry it under the same name."
                    )
                paired.add(group_name)
                interfaces.append({
                    "id": f"interface:{region_id}:{fields['group']}",
                    "name": group_name,
                    "bounded_boundary_id": boundary_id,
                    "unbounded_boundary_id": partner,
                    "topology": interface_topology(
                        domain.points_mm, domain.tetrahedra, fem_triangles, rows[group_name],
                        exterior.points_mm, exterior_triangles, exterior_rows[group_name],
                    ),
                })
    if exterior is not None:
        unpaired = sorted(
            name for name, kind in exterior.kinds.items()
            if kind.get("kind") == "interface" and name not in paired
        )
        if unpaired:
            raise CoupledSystemError(f"Exterior interface patches with no FEM side: {', '.join(unpaired)}.")
    if len(media) > 1:
        # BEAT couples regions that share one medium.
        raise CoupledSystemError("Every FEM region must use the same speed of sound and density.")
    if exterior is not None and media:
        (speed, density), = media
        region_records[0]["sound_speed_m_per_s"] = speed
        region_records[0]["density_kg_per_m3"] = density
    if not moving_by_channel:
        raise CoupledSystemError("The coupled system has no driven surface.")

    unknown = set(transducers or {}) - set(moving_by_channel)
    if unknown:
        raise CoupledSystemError(f"Transducer channels have no moving boundary: {sorted(unknown)}")
    components, ports = [], []
    projected_areas = {}
    driver_sd = {}
    area_warnings: list[str] = []
    channel_warnings: dict[str, list[str]] = {}
    for channel, boundary_ids in sorted(moving_by_channel.items()):
        component_id = f"component:{channel}"
        spec = (transducers or {}).get(channel)
        params = {}
        if spec is not None:
            from server.solver.beat_adapter.coupled_transducers import parameters

            channel_warnings[channel] = []
            driver_sd[channel] = {}
            params, projected_areas[channel] = parameters(
                spec, moving_triangles[channel], collected_warnings=channel_warnings[channel],
                channel_id=channel, sd_metadata=driver_sd[channel],
                face_weights=moving_triangle_weights[channel])
            area_warnings.extend(channel_warnings[channel])
        # Only a non-unit weight is sent, so a system without levels or patch
        # weights compiles exactly as before weights existed.
        if any(motion_weights[bid] != 1.0 for bid in boundary_ids):
            params["boundary_motion_weights"] = {bid: motion_weights[bid] for bid in sorted(boundary_ids)}
        components.append({
            "id": component_id, "name": channel,
            "kind": "electrodynamic_transducer" if spec is not None else "ideal_velocity_source",
            "boundary_ids": sorted(boundary_ids), "parameters": params,
        })
        ports.append({"id": f"port:{channel}", "name": channel,
                      "component_id": component_id, "kind": "voltage" if spec is not None else "normal_velocity"})
    return {
        "id": system_id,
        "name": name,
        "contract_version": CONTRACT_VERSION,
        "meshes": meshes,
        "regions": region_records,
        "boundaries": boundaries,
        "interfaces": interfaces,
        "components": components,
        "excitation_ports": ports,
        "assumptions": [],
        "metadata": {"producer": "waveguide-generator", "feature": "fem-regions-v1",
                     **({"projected_areas_m2": projected_areas, "driver_sd": driver_sd} if projected_areas else {}),
                     **({"warnings": area_warnings, "channel_warnings": channel_warnings} if area_warnings else {})},
    }


def solve_kind(system: Mapping[str, Any]) -> str:
    """BEAT's solve kind for a compiled system."""

    kinds = {region["kind"] for region in system["regions"]}
    if kinds == {"bounded_air"}:
        return "interior_fem"
    if kinds == {"unbounded_air"}:
        return "exterior_bem"
    return "coupled_fem_bem_lem"
