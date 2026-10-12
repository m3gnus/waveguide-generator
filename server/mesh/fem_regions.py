"""Tetrahedral meshes for declared FEM regions (``fem-regions-v1``).

A CAD return may declare bounded air -- a rear chamber, a port, a phase plug --
as a FEM region: one closed solid, exported on its own as a STEP member, with
named surface groups whose kinds are boundary conditions (``moving``,
``interface``, ``plane-wave-termination``, ``rigid``). This module meshes one
region in an open gmsh session and owns every physical tag it writes, the way
``server/mesh/imported.py`` owns the exterior's.

CAD describes each grouped face by geometry (area, centroid, normal, bounding
box, in millimetres of the export frame), because STEP carries no stable face
identity. Each description must match exactly one boundary surface of the
imported solid, and no surface may be claimed twice: a diaphragm that matched
nothing would solve to silence and an interface that matched nothing would
seal the chamber, so either refuses by group name rather than becoming a wall.

Physical names are ``wg-fem-v1|region=<id>|group=<id>|kind=<kind>`` for
surfaces and ``wg-fem-v1|region=<id>|volume`` for the air, so a mesh read back
later names its own boundary conditions. Every boundary surface no group
claims joins ``group=_walls|kind=rigid``. The file is Gmsh 4.1 ASCII with
first-order tetrahedra and tagged boundary triangles -- what the coupled BEAT
contract and Boundary Lab expect of a FEM volume.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from server.platform.temp_session import temporary_directory_root

FEM_TAG_NAMESPACE = "wg-fem-v1"
# The protocol permits letters, digits and hyphens, so this cannot collide
# with a CAD-declared group (including a user group called "walls").
WALLS_GROUP_ID = "_walls"
SYMMETRY_GROUP_ID = "_symmetry"
# A local size blends back to the region size over this many region sizes.
SIZE_BLEND_FACTOR = 3.0
# Acceptance of a face description against an imported surface. Areas and
# centroids survive the STEP round trip to well under a percent; the centroid
# tolerance is relative to the face's own extent so small faces stay strict.
AREA_TOLERANCE = 0.02
CENTROID_TOLERANCE = 0.05
NORMAL_TOLERANCE_DEG = 10.0
# The second-best candidate must be clearly worse, or the match is ambiguous.
AMBIGUITY_MARGIN = 2.0


class FemRegionMeshError(ValueError):
    """An actionable refusal, optionally identifying a rejected mirror plane."""

    def __init__(self, message: str, *, symmetry_plane: str | None = None) -> None:
        self.symmetry_plane = symmetry_plane
        super().__init__(message)


def physical_name(region_id: str, group_id: str | None = None, kind: str | None = None) -> str:
    if group_id is None:
        return f"{FEM_TAG_NAMESPACE}|region={region_id}|volume"
    return f"{FEM_TAG_NAMESPACE}|region={region_id}|group={group_id}|kind={kind}"


def parse_physical_name(name: str) -> dict[str, str] | None:
    """Split a ``wg-fem-v1`` physical name; ``None`` for anything else."""

    parts = name.split("|")
    if not parts or parts[0] != FEM_TAG_NAMESPACE:
        return None
    fields: dict[str, str] = {}
    for part in parts[1:]:
        key, separator, value = part.partition("=")
        if separator:
            fields[key] = value
        else:
            fields["entity"] = key
    return fields


@dataclass(frozen=True)
class SurfaceDescriptor:
    tag: int
    area_mm2: float
    centroid_mm: tuple[float, float, float]
    bbox_mm: tuple[tuple[float, float, float], tuple[float, float, float]]
    normal: tuple[float, float, float] | None


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _extent(bbox: Sequence[Sequence[float]]) -> float:
    return max(_distance(bbox[0], bbox[1]), 1e-9)


def match_score(face: Mapping[str, Any], surface: SurfaceDescriptor) -> float | None:
    """How far one face description is from one surface; ``None`` if rejected.

    The score adds the relative area error and the centroid distance relative
    to the face's extent. A stated normal must agree in direction up to sign
    (an imported surface's orientation is not the CAD face's), and the boxes
    must agree to the same relative tolerance as the centroid.
    """

    area = float(face["area_mm2"])
    area_error = abs(surface.area_mm2 - area) / max(area, 1e-12)
    if area_error > AREA_TOLERANCE:
        return None
    extent = _extent(face["bbox_mm"])
    centroid_error = _distance(surface.centroid_mm, face["centroid_mm"]) / extent
    if centroid_error > CENTROID_TOLERANCE:
        return None
    box_error = max(
        _distance(surface.bbox_mm[0], face["bbox_mm"][0]),
        _distance(surface.bbox_mm[1], face["bbox_mm"][1]),
    ) / extent
    if box_error > CENTROID_TOLERANCE:
        return None
    normal = face.get("normal")
    if normal is not None and surface.normal is not None:
        cosine = abs(sum(float(a) * float(b) for a, b in zip(normal, surface.normal)))
        if cosine < math.cos(math.radians(NORMAL_TOLERANCE_DEG)):
            return None
    return area_error + centroid_error + 0.1 * box_error


def assign_faces(
    region: Mapping[str, Any], surfaces: Sequence[SurfaceDescriptor]
) -> dict[str, list[int]]:
    """Match every declared face to one surface; group id -> surface tags."""

    claimed: dict[int, str] = {}
    assigned: dict[str, list[int]] = {}
    region_name = str(region.get("name") or region.get("region_id"))
    for group in region.get("surface_groups") or ():
        group_id = str(group["group_id"])
        label = f"surface group {group.get('name', group_id)!r} of FEM region {region_name!r}"
        tags: list[int] = []
        for index, face in enumerate(group.get("faces") or ()):
            scored = sorted(
                (score, surface.tag)
                for surface in surfaces
                if (score := match_score(face, surface)) is not None
            )
            if not scored:
                raise FemRegionMeshError(
                    f"Face {index + 1} of {label} matches no surface of the exported "
                    "solid. Re-declare the group in Fusion and send again."
                )
            best, tag = scored[0]
            if len(scored) > 1 and scored[1][0] <= max(best * AMBIGUITY_MARGIN, 1e-6):
                raise FemRegionMeshError(
                    f"Face {index + 1} of {label} matches more than one surface of "
                    "the exported solid equally well."
                )
            owner = claimed.get(tag)
            if owner is not None:
                raise FemRegionMeshError(
                    f"Two declared faces ({owner} and {label}) match the same surface."
                )
            claimed[tag] = label
            tags.append(tag)
        assigned[group_id] = sorted(tags)
    return assigned


def _surface_descriptors(gmsh: Any, tags: Sequence[int]) -> list[SurfaceDescriptor]:
    descriptors = []
    for tag in tags:
        low = gmsh.model.getBoundingBox(2, tag)
        normal = None
        if gmsh.model.getType(2, tag) == "Plane":
            parametric = gmsh.model.getParametrizationBounds(2, tag)
            middle = [(parametric[0][0] + parametric[1][0]) / 2, (parametric[0][1] + parametric[1][1]) / 2]
            vector = gmsh.model.getNormal(tag, middle)
            length = math.sqrt(sum(value * value for value in vector)) or 1.0
            normal = tuple(float(value) / length for value in vector)
        descriptors.append(
            SurfaceDescriptor(
                tag=int(tag),
                area_mm2=float(gmsh.model.occ.getMass(2, tag)),
                centroid_mm=tuple(float(value) for value in gmsh.model.occ.getCenterOfMass(2, tag)),
                bbox_mm=(tuple(low[:3]), tuple(low[3:])),
                normal=normal,
            )
        )
    return descriptors


def _size_fields(gmsh: Any, region_size: float, groups: Sequence[tuple[list[int], float]]) -> int:
    fields = []
    for tags, size in groups:
        if not tags or size >= region_size:
            continue
        distance = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(distance, "SurfacesList", tags)
        gmsh.model.mesh.field.setNumber(distance, "Sampling", 60)
        threshold = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(threshold, "InField", distance)
        gmsh.model.mesh.field.setNumber(threshold, "SizeMin", size)
        gmsh.model.mesh.field.setNumber(threshold, "SizeMax", region_size)
        gmsh.model.mesh.field.setNumber(threshold, "DistMin", 0.0)
        gmsh.model.mesh.field.setNumber(threshold, "DistMax", SIZE_BLEND_FACTOR * region_size)
        fields.append(threshold)
    if not fields:
        return 0
    if len(fields) == 1:
        gmsh.model.mesh.field.setAsBackgroundMesh(fields[0])
    else:
        minimum = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(minimum, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(minimum)
    return len(fields)


def symmetry_surfaces(gmsh: Any, tags: Sequence[int], planes: Sequence[str]) -> set[int]:
    """Planar caps on the exterior route's active, origin-centred planes."""
    axes = {"x0": 0, "y0": 1}
    if any(plane not in axes for plane in planes):
        raise FemRegionMeshError("FEM symmetry supports only x0 and y0")
    return {tag for tag in tags if any(
        gmsh.model.getType(2, tag) == "Plane"
        and max(abs(gmsh.model.getBoundingBox(2, tag)[axes[plane]]),
                abs(gmsh.model.getBoundingBox(2, tag)[axes[plane] + 3])) < 1e-5
        for plane in planes)}


def verify_region_symmetry(gmsh: Any, region: Mapping[str, Any], boundary: Sequence[int],
                           assigned: Mapping[str, Sequence[int]], planes: Sequence[str],
                           cut_planes: Sequence[str]) -> None:
    """Reuse the exterior's trimmed OCC geometry/role mirror test before cutting."""
    import numpy as np
    from hornlab_mesher.step_prepare import (
        AUTO_CUT_SPAN_REL, DEFAULT_AUTO_CUT_TOLERANCE_REL,
        evaluate_occ_plane_symmetry, sample_occ_surface_points,
    )
    if not planes:
        return
    axes = {"x0": 0, "y0": 1}
    if len(set(planes)) != len(planes) or any(p not in axes for p in planes):
        raise FemRegionMeshError("FEM symmetry supports only x0, y0, or x0+y0")
    samples = {tag: sample_occ_surface_points(tag, grid=7) for tag in boundary}
    stacked = np.concatenate(list(samples.values()))
    diagonal = float(np.linalg.norm(np.ptp(stacked, axis=0)))
    roles = {tag: WALLS_GROUP_ID for tag in boundary}
    for group, tags in assigned.items():
        roles.update({tag: group for tag in tags})
    bboxes = {tag: tuple(gmsh.model.getBoundingBox(2, tag)) for tag in boundary}
    for plane in planes:
        if plane not in cut_planes:
            if stacked[:, axes[plane]].min() < -1e-5:
                raise FemRegionMeshError(
                    f"FEM region {region['region_id']!r} lies outside the positive author-cut domain on {plane}")
            continue
        verdict = evaluate_occ_plane_symmetry(
            plane, samples=samples, roles=roles, bboxes=bboxes,
            tolerance=DEFAULT_AUTO_CUT_TOLERANCE_REL * diagonal,
            span_tolerance=AUTO_CUT_SPAN_REL * diagonal)
        if not verdict.accepted:
            raise FemRegionMeshError(
                f"FEM region {region['region_id']!r} does not mirror on {plane}: {verdict.reason}",
                symmetry_plane=plane)


def fragment_fundamental_domain(gmsh: Any, objects: list[tuple[int, int]],
                                tools: list[tuple[int, int]], planes: Sequence[str]
                                ) -> dict[tuple[int, int], set[int]]:
    """The exterior positive half-space box, with volume ancestry retained for FEM.

    Fragment the half-space together with material, air and named surfaces once;
    discard its outside pieces before meshing. Unlike the surface-only exterior
    cutter, keep volume roots and their new rigid plane caps.
    """
    import numpy as np
    from hornlab_mesher.step_prepare import (
        AUTO_CUT_BOX_PAD_REL, SYMMETRY_AXIS_FOR_PLANE, _claim_evidence,
    )

    inputs = objects + tools
    clipping = []
    copies = {}
    if planes:
        bbox = gmsh.model.getBoundingBox(-1, -1)
        lo, hi = np.array(bbox[:3]), np.array(bbox[3:])
        pad = AUTO_CUT_BOX_PAD_REL * max(hi - lo) + 1.
        lo, hi = lo - pad, hi + pad
        for plane in planes:
            lo[SYMMETRY_AXIS_FOR_PLANE[plane]] = 0.
        # The exterior cutter guards against OCC recycling a removed parent's
        # tag for another face. Preserve the same trimmed-geometry evidence for
        # named FEM faces and painted source copies during volume fragmentation.
        copies = {tag: gmsh.model.occ.copy([(2, tag)])[0][1]
                  for dim, tag in inputs if dim == 2}
        clipping = [(3, gmsh.model.occ.addBox(*lo.tolist(), *(hi - lo).tolist()))]
    _out, ancestry = gmsh.model.occ.fragment(objects, tools + clipping)
    gmsh.model.occ.synchronize()

    def retained(dim: int, tag: int) -> bool:
        center = gmsh.model.occ.getCenterOfMass(dim, tag)
        return all(center[SYMMETRY_AXIS_FOR_PLANE[p]] >= -1e-7 for p in planes)

    descendants = {
        entity: {int(tag) for dim, tag in mapped if dim == entity[0] and retained(dim, tag)}
        for entity, mapped in zip(inputs, ancestry)
    }
    if planes:
        try:
            for parent, copy in copies.items():
                descendants[(2, parent)] = {
                    child for child in descendants[(2, parent)]
                    if _claim_evidence(copy, child, tolerance=1e-5, grid=7) != "off"
                }
        finally:
            gmsh.model.occ.remove([(2, tag) for tag in copies.values()], recursive=True)
            gmsh.model.occ.synchronize()
        volumes = set().union(*(tags for (dim, _tag), tags in descendants.items() if dim == 3))
        discard = [(3, tag) for _dim, tag in gmsh.model.getEntities(3) if tag not in volumes]
        gmsh.model.occ.remove(discard, recursive=False)
        gmsh.model.occ.synchronize()
        surfaces = {tag for dim, tag in gmsh.model.getBoundary(
            [(3, tag) for tag in volumes], combined=False, oriented=False) if dim == 2}
        surfaces.update(set().union(*(tags for (dim, _tag), tags in descendants.items() if dim == 2)))
        gmsh.model.occ.remove([(2, tag) for _dim, tag in gmsh.model.getEntities(2)
                               if tag not in surfaces], recursive=True)
        gmsh.model.occ.synchronize()
    return descendants


def build_fem_region_mesh(
    step_path: str | Path, region: Mapping[str, Any], *, scratch_dir: str | Path | None = None,
    normalisation_matrix: Any = None, domain_planes: Sequence[str] = (),
    cut_planes: Sequence[str] = (),
) -> dict[str, Any]:
    """Mesh one validated ``scope.fem_regions[]`` record in the open session.

    The STEP is in millimetres of the export frame, the same frame as the face
    descriptions, so they are compared directly. Returns the MSH 4.1 text,
    the physical-name table, which surfaces each group claimed, and counts.
    """

    import gmsh

    region_id = str(region["region_id"])
    region_size = float(region["mesh_size_mm"])
    gmsh.model.add(f"fem-region-{region_id}")
    try:
        # OpenCASCADE reads STEP in millimetres, as the exterior import does.
        imported = gmsh.model.occ.importShapes(str(step_path))
        gmsh.model.occ.synchronize()
        volumes = sorted({tag for dim, tag in imported if dim == 3}) or sorted(
            tag for _dim, tag in gmsh.model.getEntities(3)
        )
        if len(volumes) != 1:
            raise FemRegionMeshError(
                f"FEM region {region.get('name', region_id)!r} must be exactly one closed "
                f"solid; its STEP holds {len(volumes)}."
            )
        boundary = sorted(
            {tag for dim, tag in gmsh.model.getBoundary([(3, volumes[0])], oriented=False) if dim == 2}
        )
        assigned = assign_faces(region, _surface_descriptors(gmsh, boundary))

        if normalisation_matrix is not None:
            from server.mesh.imported import apply_rigid_normalisation
            apply_rigid_normalisation(gmsh, [(3, volumes[0])], normalisation_matrix)
        verify_region_symmetry(gmsh, region, boundary, assigned, domain_planes, cut_planes)
        if domain_planes:
            copies = {tag: gmsh.model.occ.copy([(2, tag)])[0][1] for tag in boundary}
            descendants = fragment_fundamental_domain(
                gmsh, [(3, volumes[0])], [(2, tag) for tag in copies.values()], domain_planes)
            volumes = sorted(descendants[(3, volumes[0])])
            assigned = {group: sorted(set().union(*(descendants[(2, copies[tag])] for tag in tags)))
                        for group, tags in assigned.items()}
            boundary = sorted({tag for dim, tag in gmsh.model.getBoundary(
                [(3, tag) for tag in volumes], combined=False, oriented=False) if dim == 2})
        caps = symmetry_surfaces(gmsh, boundary, domain_planes)
        if any(caps.intersection(tags) for tags in assigned.values()):
            raise FemRegionMeshError(f"FEM region {region_id!r} declares a boundary on a symmetry plane")
        names: dict[str, dict[str, Any]] = {}
        volume_name = physical_name(region_id)
        tag = gmsh.model.addPhysicalGroup(3, volumes)
        gmsh.model.setPhysicalName(3, tag, volume_name)
        names[volume_name] = {"dimension": 3, "tag": tag, "entities": volumes}
        sized: list[tuple[list[int], float]] = []
        claimed: set[int] = set()
        for group in region.get("surface_groups") or ():
            group_id = str(group["group_id"])
            surfaces = assigned[group_id]
            if not surfaces:
                raise FemRegionMeshError(f"FEM region {region_id!r}, group {group_id!r} disappeared under symmetry")
            claimed.update(surfaces)
            name = physical_name(region_id, group_id, str(group["kind"]))
            tag = gmsh.model.addPhysicalGroup(2, surfaces)
            gmsh.model.setPhysicalName(2, tag, name)
            names[name] = {
                "dimension": 2,
                "tag": tag,
                "entities": surfaces,
                "group_id": group_id,
                "kind": str(group["kind"]),
                **({"drive_channel_id": group["drive_channel_id"]} if group.get("drive_channel_id") else {}),
            }
            sized.append((surfaces, float(group["mesh_size_mm"])))
        if caps:
            name = physical_name(region_id, SYMMETRY_GROUP_ID, "rigid")
            tag = gmsh.model.addPhysicalGroup(2, sorted(caps))
            gmsh.model.setPhysicalName(2, tag, name)
            names[name] = {"dimension": 2, "tag": tag, "entities": sorted(caps),
                           "group_id": SYMMETRY_GROUP_ID, "kind": "rigid"}
        walls = [surface for surface in boundary if surface not in claimed and surface not in caps]
        if walls:
            name = physical_name(region_id, WALLS_GROUP_ID, "rigid")
            tag = gmsh.model.addPhysicalGroup(2, walls)
            gmsh.model.setPhysicalName(2, tag, name)
            names[name] = {"dimension": 2, "tag": tag, "entities": walls,
                           "group_id": WALLS_GROUP_ID, "kind": "rigid"}

        smallest = min([region_size, *(size for _tags, size in sized)])
        gmsh.option.setNumber("Mesh.MeshSizeMin", smallest)
        gmsh.option.setNumber("Mesh.MeshSizeMax", region_size)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        fields = _size_fields(gmsh, region_size, sized)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.model.mesh.generate(3)

        element_types, element_tags, _nodes = gmsh.model.mesh.getElements(3)
        tetrahedra = sum(len(tags) for kind, tags in zip(element_types, element_tags) if kind == 4)
        if tetrahedra == 0:
            raise FemRegionMeshError(
                f"FEM region {region.get('name', region_id)!r} produced no tetrahedra."
            )
        node_tags, _coords, _parametric = gmsh.model.mesh.getNodes()
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        with tempfile.TemporaryDirectory(prefix="wg-fem-", dir=scratch_dir or temporary_directory_root()) as folder:
            target = Path(folder) / "region.msh"
            gmsh.write(str(target))
            msh_text = target.read_text(encoding="utf-8")
        return {
            "region_id": region_id,
            "msh_text": msh_text,
            "physical_names": names,
            **({"normalisation_matrix": normalisation_matrix} if normalisation_matrix is not None else {}),
            "stats": {
                "tetrahedra": int(tetrahedra),
                "nodes": int(len(node_tags)),
                "boundary_surfaces": len(boundary),
                "wall_surfaces": len(walls),
                "size_fields": fields,
            },
        }
    finally:
        gmsh.model.remove()


# ---------------------------------------------------------------- read back


@dataclass(frozen=True)
class FemRegionDomain:
    """One meshed region as solver and viewer read it, in millimetres."""

    region_id: str
    points_mm: Any  # (N, 3) float64
    tetrahedra: Any  # (T, 4) int64, zero-based
    surfaces: Mapping[str, Any]  # physical name -> (F, 3) int64 triangles

    def groups(self) -> dict[str, dict[str, str]]:
        found = {}
        for name in self.surfaces:
            fields = parse_physical_name(name)
            if fields is not None and "group" in fields:
                found[name] = fields
        return found


def read_fem_region_domain(
    msh_text: str, region_id: str, *, scratch_dir: str | Path | None = None,
) -> FemRegionDomain:
    """Parse a region mesh written by :func:`build_fem_region_mesh`."""

    import meshio
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="wg-fem-read-", dir=scratch_dir or temporary_directory_root()) as folder:
        path = Path(folder) / "region.msh"
        path.write_text(msh_text, encoding="utf-8")
        mesh = meshio.read(path, file_format="gmsh")
    names_by_tag: dict[tuple[int, int], str] = {
        (int(dimension), int(tag)): name for name, (tag, dimension) in mesh.field_data.items()
    }
    physical = mesh.cell_data.get("gmsh:physical") or []
    tetrahedra = []
    surfaces: dict[str, list[Any]] = {}
    for index, block in enumerate(mesh.cells):
        if block.type == "tetra":
            tetrahedra.append(np.asarray(block.data, dtype=np.int64))
        elif block.type == "triangle":
            if index >= len(physical) or len(physical[index]) != len(block.data):
                raise FemRegionMeshError(
                    f"FEM region {region_id!r} has boundary triangles with no group tags."
                )
            tags = physical[index]
            for tag in np.unique(tags):
                name = names_by_tag.get((2, int(tag)))
                fields = parse_physical_name(name) if name is not None else None
                if fields is None or fields.get("region") != region_id or "group" not in fields:
                    raise FemRegionMeshError(
                        f"FEM region {region_id!r} boundary group tag {int(tag)} has no valid physical name."
                    )
                surfaces.setdefault(name, []).append(
                    np.asarray(block.data[np.asarray(tags) == tag], dtype=np.int64)
                )
    if not tetrahedra:
        raise FemRegionMeshError(f"FEM region {region_id!r} mesh holds no tetrahedra.")
    domain = FemRegionDomain(
        region_id=region_id,
        points_mm=np.asarray(mesh.points, dtype=np.float64),
        tetrahedra=np.concatenate(tetrahedra),
        surfaces={name: np.concatenate(blocks) for name, blocks in surfaces.items()},
    )

    _validate_boundary_coverage(domain)
    return domain


def _validate_boundary_coverage(domain: FemRegionDomain) -> None:
    """Require exactly one named triangle for every exterior tetrahedral facet."""

    import numpy as np

    tets = domain.tetrahedra
    facets = np.concatenate([tets[:, [0, 1, 2]], tets[:, [0, 1, 3]],
                             tets[:, [0, 2, 3]], tets[:, [1, 2, 3]]])
    unique, counts = np.unique(np.sort(facets, axis=1), axis=0, return_counts=True)
    boundary = unique[counts == 1]
    triangles = (np.concatenate(list(domain.surfaces.values())) if domain.surfaces
                 else np.empty((0, 3), dtype=np.int64))
    named, named_counts = np.unique(np.sort(triangles, axis=1), axis=0, return_counts=True)
    if np.any(named_counts != 1) or not np.array_equal(named, boundary):
        groups = ", ".join(repr(fields["group"]) for fields in domain.groups().values()) or "<none>"
        raise FemRegionMeshError(
            f"FEM region {domain.region_id!r}, surface groups {groups}: named surface "
            "triangles do not cover the whole region boundary exactly once."
        )


def fem_region_mesh_stats(
    built: Mapping[str, Any], region: Mapping[str, Any], *, scratch_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the retained mesh and measure it inside the supervised child."""

    import numpy as np

    region_id = str(region["region_id"])
    try:
        domain = read_fem_region_domain(built["msh_text"], region_id, scratch_dir=scratch_dir)
    except FemRegionMeshError as exc:
        labels = ", ".join(
            f"{group.get('name', group['group_id'])!r} ({group['group_id']!r})"
            for group in region.get("surface_groups") or ()
        )
        raise FemRegionMeshError(f"FEM region {region_id!r}, declared surface groups {labels}: {exc}") from exc
    groups: dict[str, dict[str, Any]] = {}
    for name, triangles in domain.surfaces.items():
        physical = built["physical_names"][name]
        group_id = physical["group_id"]
        if group_id in groups:
            raise FemRegionMeshError(f"FEM region {region_id!r}, group {group_id!r}: duplicate mesh group.")
        corners = domain.points_mm[triangles]
        area = 0.5 * np.linalg.norm(
            np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1,
        ).sum()
        groups[group_id] = {
            "kind": parse_physical_name(name)["kind"],
            "area_mm2": float(area),
            "face_count": len(physical["entities"]),
            "triangle_count": int(len(triangles)),
        }
    for group in region.get("surface_groups") or ():
        found = groups.get(group["group_id"])
        if found is None or found["triangle_count"] <= 0 or found["kind"] != group["kind"]:
            raise FemRegionMeshError(
                f"FEM region {region_id!r}, surface group {group.get('name', group['group_id'])!r} "
                f"({group['group_id']!r}): mesh must retain triangles of declared kind {group['kind']!r}."
            )
    return {**built["stats"], "tetrahedra": int(len(domain.tetrahedra)),
            "nodes": int(len(domain.points_mm)), "surface_groups": groups}
