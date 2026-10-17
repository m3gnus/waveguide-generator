"""Test-only reader that turns Boundary Lab's SAWMOD fixture meshes into WG inputs (V11).

The gmsh 2.2 files are fetched at test time as DATA only; no Boundary Lab code or
text lives in this repository. This module is WG's own reading of them:

* FEM volume ``SMfemvolume_*.msh``: tetrahedra plus triangle groups
  ``INTERFACE`` (exterior coupling), ``HF`` and ``MF`` (diaphragms),
  ``SYMMETRY`` (cut-plane caps of the reduced meshes) and ``*_boundary``
  (rigid walls). Reduced files repeat the names with `` (1)`` suffixes.
* Exterior ``SMexterior_*.msh``: ``BEMInterface`` (the mouth patch facing the
  FEM ``INTERFACE``) and ``BEMExt`` (the rigid enclosure).

WG couples only conforming meshes: every FEM interface triangle must be an
exterior triangle. The published exterior's ``BEMInterface`` covers the same
surface (same area to 1e-4) but is triangulated independently, and its rim
differs from the FEM rim along the four rounded mouth corners. This adapter
therefore builds the exterior from ``BEMExt`` plus the FEM ``INTERFACE``
triangles themselves, welds coincident vertices, and re-triangulates only the
planar ``BEMExt`` ledge triangles that touch a rim vertex the FEM rim lacks.
The FEM mesh is never modified. Any non-planar repair is refused.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

import numpy as np

from server.mesh.fem_regions import FemRegionDomain, _validate_boundary_coverage, physical_name
from server.solver.coupled_system import ExteriorSurface, FemRegionInput

REGION_ID = "sawmod"
CHANNELS = {"HF": "drive-hf", "MF": "drive-mf"}
EXTERIOR_RIGID = "bl-sawmod|exterior|kind=rigid"
SYMMETRY_MODES = {(): "full", (0,): "yz", (0, 1): "yz+xz"}
WELD_TOLERANCE_MM = 1e-4  # Published duplicates differ by ~3e-7 mm; real nodes by > 0.1 mm.
PLANE_SNAP_MM = 1e-6


class SawmodAdapterError(ValueError):
    """The fetched meshes do not have the structure this adapter understands."""


@dataclass(frozen=True)
class GmshMesh:
    points_mm: np.ndarray
    tetrahedra: np.ndarray
    triangles: dict[str, np.ndarray]  # physical name, suffixes stripped -> (F, 3)


def base_name(name: str) -> str:
    """``'SAWMODFEM (1) (1)_boundary'`` -> ``'SAWMODFEM_boundary'``; ``'HF (1)'`` -> ``'HF'``."""
    return re.sub(r"(?: \(\d+\))+", "", name)


def fem_role(name: str) -> tuple[str, str, str | None]:
    """(WG group id, WG kind, drive channel) for one Boundary Lab FEM group name."""
    name = base_name(name)
    if name == "INTERFACE":
        return "interface", "interface", None
    if name in CHANNELS:
        return name.lower(), "moving", CHANNELS[name]
    if name == "SYMMETRY":
        return "_symmetry", "rigid", None
    if name.endswith("_boundary"):
        return "walls", "rigid", None
    raise SawmodAdapterError(f"Unknown SAWMOD FEM group {name!r}")


def read_gmsh(path: str | Path, symmetry_axes: tuple[int, ...] = ()) -> GmshMesh:
    """Points [mm], zero-based tetrahedra and named triangles; snap cut-plane coordinates to 0."""
    import meshio

    mesh = meshio.read(str(path), file_format="gmsh")
    names = {(int(dim), int(tag)): name for name, (tag, dim) in mesh.field_data.items()}
    physical = mesh.cell_data.get("gmsh:physical")
    if physical is None:
        raise SawmodAdapterError(f"{Path(path).name}: no physical groups")
    points = np.asarray(mesh.points, dtype=np.float64).copy()
    for axis in symmetry_axes:
        points[np.abs(points[:, axis]) <= PLANE_SNAP_MM, axis] = 0.
    points[points == 0] = 0.  # Drop negative zeros so exact plane tests are sign-free.
    tetrahedra, triangles = [], {}
    for block, tags in zip(mesh.cells, physical):
        if block.type == "tetra":
            tetrahedra.append(np.asarray(block.data, dtype=np.int64))
        elif block.type == "triangle":
            for tag in np.unique(tags):
                name = names.get((2, int(tag)))
                if name is None:
                    raise SawmodAdapterError(f"{Path(path).name}: triangle tag {tag} has no physical name")
                triangles.setdefault(base_name(name), []).append(np.asarray(block.data[tags == tag], dtype=np.int64))
    return GmshMesh(points, np.concatenate(tetrahedra) if tetrahedra else np.zeros((0, 4), np.int64),
                    {name: np.concatenate(blocks) for name, blocks in triangles.items()})


def fem_region(path: str | Path, symmetry_axes: tuple[int, ...] = ()) -> FemRegionInput:
    """One WG FEM region; both diaphragms are ideal (unit-acceleration) drive channels."""
    mesh = read_gmsh(path, symmetry_axes)
    if not len(mesh.tetrahedra):
        raise SawmodAdapterError(f"{Path(path).name}: no tetrahedra")
    corners = mesh.points_mm[mesh.tetrahedra]
    if np.any(np.linalg.det(corners[:, 1:] - corners[:, :1]) <= 0):
        raise SawmodAdapterError(f"{Path(path).name}: tetrahedra must be positively oriented")
    surfaces, declarations = {}, {}
    for name, faces in mesh.triangles.items():
        group, kind, channel = fem_role(name)
        key = physical_name(REGION_ID, group, kind)
        surfaces[key] = np.concatenate([surfaces[key], faces]) if key in surfaces else faces
        if group != "_symmetry":
            declarations[group] = {"group_id": group, "kind": kind,
                                   **({"drive_channel_id": channel} if channel else {})}
    if bool(symmetry_axes) != (physical_name(REGION_ID, "_symmetry", "rigid") in surfaces):
        raise SawmodAdapterError(f"{Path(path).name}: SYMMETRY caps must exist exactly on reduced meshes")
    domain = FemRegionDomain(REGION_ID, mesh.points_mm, mesh.tetrahedra, surfaces)
    _validate_boundary_coverage(domain)
    record = {"region_id": REGION_ID, "name": "SAWMOD FEM volume",
              "medium": {"sound_speed_m_per_s": 343., "density_kg_per_m3": 1.21, "bulk_loss_factor": 0.},
              "surface_groups": sorted(declarations.values(), key=lambda g: g["group_id"])}
    return FemRegionInput(record, domain)


def _edges(triangles):
    edges = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    return unique[counts == 1], unique[counts > 2]


def _off_plane(points, edges, axes):
    on = np.zeros(len(edges), bool)
    for axis in axes:
        on |= np.all(points[edges, axis] == 0, axis=1)
    return edges[~on]


def _weld(points, tolerance, preferred):
    """Union coincident vertices; a preferred (FEM) vertex is the representative."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    pairs = cKDTree(points).query_pairs(tolerance, output_type="ndarray")
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(points),) * 2) \
        if len(pairs) else coo_matrix((len(points), len(points)))
    _, labels = connected_components(graph, directed=False)
    representative = {}
    for index in np.argsort(~preferred, kind="stable"):  # preferred vertices first
        representative.setdefault(int(labels[index]), int(index))
    return np.array([representative[int(label)] for label in labels])


def _ear_clip(loop, xy):
    """Triangulate one simple planar polygon given as an ordered vertex loop."""
    loop = list(loop)
    area = sum(xy[a, 0] * xy[b, 1] - xy[b, 0] * xy[a, 1] for a, b in zip(loop, loop[1:] + loop[:1]))
    if area < 0:
        loop.reverse()
    triangles = []

    def cross(o, a, b):
        return (xy[a, 0] - xy[o, 0]) * (xy[b, 1] - xy[o, 1]) - (xy[a, 1] - xy[o, 1]) * (xy[b, 0] - xy[o, 0])

    guard = 0
    while len(loop) > 3:
        guard += 1
        if guard > 10 * len(loop) ** 2:
            raise SawmodAdapterError("Ear clipping failed: the repair polygon is not simple")
        for i in range(len(loop)):
            a, b, c = loop[i - 1], loop[i], loop[(i + 1) % len(loop)]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0
                   for p in loop if p not in (a, b, c)):
                continue
            triangles.append((a, b, c))
            loop.pop(i)
            break
    triangles.append(tuple(loop))
    return triangles


def _loops(edges):
    neighbours: dict[int, list[int]] = {}
    for a, b in edges.tolist():
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    if any(len(n) != 2 for n in neighbours.values()):
        raise SawmodAdapterError("Exterior repair holes are not simple loops")
    loops, seen = [], set()
    for start in neighbours:
        if start in seen:
            continue
        loop, previous, current = [start], None, start
        seen.add(start)
        while True:
            following = next(n for n in neighbours[current] if n != previous)
            if following == start:
                break
            loop.append(following)
            seen.add(following)
            previous, current = current, following
        loops.append(loop)
    return loops


def exterior_surface(path: str | Path, fem: FemRegionInput, symmetry_axes: tuple[int, ...] = ()):
    """Closed WG exterior conforming to ``fem``'s interface; returns (surface, repair report)."""
    mesh = read_gmsh(path, symmetry_axes)
    if set(mesh.triangles) != {"BEMInterface", "BEMExt"}:
        raise SawmodAdapterError(f"{Path(path).name}: expected BEMInterface and BEMExt, got {sorted(mesh.triangles)}")
    interface_name = physical_name(REGION_ID, "interface", "interface")
    fem_faces = fem.domain.surfaces[interface_name]
    offset = len(mesh.points_mm)
    points = np.vstack([mesh.points_mm, fem.domain.points_mm])
    preferred = np.r_[np.zeros(offset, bool), np.ones(len(fem.domain.points_mm), bool)]
    welded = _weld(points, WELD_TOLERANCE_MM, preferred)
    rigid = welded[mesh.triangles["BEMExt"]]
    interface = welded[fem_faces + offset]
    rigid = rigid[(rigid[:, 0] != rigid[:, 1]) & (rigid[:, 1] != rigid[:, 2]) & (rigid[:, 0] != rigid[:, 2])]
    open_edges = _off_plane(points, _edges(np.vstack([rigid, interface]))[0], symmetry_axes)
    fem_vertices = set(np.unique(interface).tolist())
    bad = sorted(set(open_edges.ravel().tolist()) - fem_vertices)
    removed = np.any(np.isin(rigid, bad), axis=1)
    rigid = rigid[~removed]
    added = []
    if len(bad):
        holes = _off_plane(points, _edges(np.vstack([rigid, interface]))[0], symmetry_axes)
        for loop in _loops(holes):
            heights = points[loop, 2]
            if np.ptp(heights) > 1e-6:
                raise SawmodAdapterError("Exterior rim repair would need a non-planar patch")
            added.extend(_ear_clip(loop, points[:, :2]))
    if added:
        rigid = np.vstack([rigid, np.asarray(added, dtype=np.int64)])
    remaining, nonmanifold = _edges(np.vstack([rigid, interface]))
    if len(_off_plane(points, remaining, symmetry_axes)) or len(nonmanifold):
        raise SawmodAdapterError("Exterior is still open or non-manifold after the rim repair")
    surface = ExteriorSurface(points, {EXTERIOR_RIGID: rigid, interface_name: interface},
                              {EXTERIOR_RIGID: {"kind": "rigid"}, interface_name: {"kind": "interface"}})
    corners = points[mesh.triangles["BEMInterface"]]
    published_area = .5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()
    corners = points[interface]
    fem_area = .5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()
    report = {"removed_ledge_triangles": int(removed.sum()), "added_ledge_triangles": len(added),
              "unmatched_rim_vertices": len(bad), "published_interface_area_mm2": float(published_area),
              "fem_interface_area_mm2": float(fem_area)}
    if abs(published_area - fem_area) > 1e-3 * fem_area:
        raise SawmodAdapterError(f"BEMInterface and FEM INTERFACE cover different areas: {report}")
    return surface, report


def load_model(directory: str | Path, variant: str):
    """``variant`` in full/reduced_x/reduced_xy -> (exterior, FEM region, symmetry mode, repair report)."""
    axes = {"full": (), "reduced_x": (0,), "reduced_xy": (0, 1)}[variant]
    directory = Path(directory)
    fem = fem_region(directory / f"SMfemvolume_{variant}.msh", axes)
    exterior, report = exterior_surface(directory / f"SMexterior_{variant}.msh", fem, axes)
    return exterior, fem, SYMMETRY_MODES[axes], report
