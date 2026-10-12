"""Conforming exterior closure and bounded-air meshes in one OCC model.

Replay the exterior's winning import recipe, fragment material and air together,
then extract the union's skin. Internal material/air faces stay in the FEM mesh
only; exposed declared interfaces belong to both meshes. No independent surface
triangulation, coordinate welding, or projection is used at the interface.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

import numpy as np

from server.mesh.fem_regions import (
    FemRegionMeshError,
    _surface_descriptors,
    assign_faces,
    physical_name,
    read_fem_region_domain,
    SIZE_BLEND_FACTOR,
    WALLS_GROUP_ID,
    SYMMETRY_GROUP_ID,
    symmetry_surfaces,
    verify_region_symmetry,
    fragment_fundamental_domain,
)
from server.mesh.imported import (
    _geometry_fingerprint,
    _import_occ_root_bodies,
    apply_occ_healing_options,
    apply_rigid_normalisation,
    declared_step_bodies,
    rigid_occ_motion,
)
from server.mesh.surface_topology import open_edges, orient_closed_surface
from server.platform.temp_session import temporary_directory_root


def _label(region: Mapping[str, Any], group: Mapping[str, Any] | None = None) -> str:
    result = f"FEM region {region.get('name', region['region_id'])!r}"
    if group is not None:
        result += f", group {group.get('name', group['group_id'])!r}"
    return result


def _boundary(gmsh: Any, volumes: Sequence[int]) -> set[int]:
    return {
        int(tag)
        for dim, tag in gmsh.model.getBoundary(
            [(3, tag) for tag in volumes],
            combined=False,
            oriented=False,
        )
        if dim == 2
    }


def _elements(gmsh: Any, dim: int, tags: Sequence[int], remap: Mapping[int, int]) -> np.ndarray:
    width, kind = (3, 2) if dim == 2 else (4, 4)
    blocks = []
    for tag in sorted(tags):
        types, _ids, nodes = gmsh.model.mesh.getElements(dim, int(tag))
        for element_type, connectivity in zip(types, nodes):
            if int(element_type) != kind:
                raise FemRegionMeshError("Coupled mesh contains a non-first-order element.")
            blocks.append(
                np.array([remap[int(node)] for node in connectivity], dtype=np.int64).reshape(
                    -1, width
                )
            )
    return np.concatenate(blocks) if blocks else np.zeros((0, width), dtype=np.int64)


def _outward_facets(
    points: np.ndarray, triangles: np.ndarray, tetrahedra: np.ndarray
) -> np.ndarray:
    """Orient each boundary triangle away from its owning tetrahedron."""
    owners: dict[tuple[int, int, int], list[int]] = {}
    for tetra in tetrahedra:
        for opposite in range(4):
            face = tuple(sorted(np.delete(tetra, opposite).tolist()))
            owners.setdefault(face, []).append(int(tetra[opposite]))
    result = triangles.copy()
    for index, triangle in enumerate(triangles):
        opposite = owners.get(tuple(sorted(triangle.tolist())), [])
        if len(opposite) != 1:
            raise FemRegionMeshError(
                "FEM boundary triangle does not bound exactly one tetrahedron."
            )
        a, b, c = points[triangle]
        if np.dot(np.cross(b - a, c - a), points[opposite[0]] - a) > 0:
            result[index] = triangle[[0, 2, 1]]
    return result


def _closed(points: np.ndarray, triangles: np.ndarray, planes: Sequence[str] = ()) -> np.ndarray:
    edges = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    _keys, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    if not len(triangles) or np.any(counts > 2) or (not planes and np.any(counts != 2)):
        raise FemRegionMeshError(
            f"Coupled exterior is not closed: {open_edges(triangles)} open edges; "
            "every edge must bound exactly two triangles."
        )
    try:
        oriented = orient_closed_surface(points, triangles, into_body=False,
                                         symmetry_plane_axes=tuple({"x0": 0, "y0": 1}[p] for p in planes))
    except ValueError as exc:
        raise FemRegionMeshError(str(exc)) from exc
    directed = np.concatenate([oriented[:, [0, 1]], oriented[:, [1, 2]], oriented[:, [2, 0]]])
    if len(np.unique(directed, axis=0)) != len(directed):
        raise FemRegionMeshError("Coupled exterior has inconsistent interface winding.")
    return oriented


def _check_interface(points: np.ndarray, fem: np.ndarray, bem: np.ndarray, label: str) -> None:
    from scipy.spatial import cKDTree

    # Detached air volumes keep their own nodes; only acoustic ports require
    # a coordinate/connectivity map to the original exterior skin.
    vertices = np.unique(bem)
    distances, nearest = cKDTree(points[vertices]).query(points[np.unique(fem)])
    if np.any(distances > 1e-6) or len(set(nearest)) != len(nearest):
        raise FemRegionMeshError(f"{label} interface triangles are not shared.")
    mapping = dict(zip(np.unique(fem), vertices[nearest]))
    rows = {tuple(sorted(face)): index for index, face in enumerate(bem)}
    indices = [rows.get(tuple(sorted(mapping[v] for v in face))) for face in fem]
    if len(fem) != len(bem) or None in indices or len(set(indices)) != len(indices):
        raise FemRegionMeshError(f"{label} interface triangles are not shared.")
    a, b = points[fem], points[bem[indices]]
    dots = np.einsum(
        "ij,ij->i",
        np.cross(a[:, 1] - a[:, 0], a[:, 2] - a[:, 0]),
        np.cross(b[:, 1] - b[:, 0], b[:, 2] - b[:, 0]),
    )
    if np.any(dots <= 0):
        raise FemRegionMeshError(f"{label} interface winding disagrees with the exterior.")


def _detach_region(gmsh: Any, region: Mapping[str, Any], volumes: Sequence[int],
                   boundary: set[int], groups: Mapping[str, set[int]]
                   ) -> tuple[list[int], set[int], dict[str, set[int]]]:
    """Copy a touching air domain; constrain only its FEM/BEM port meshes."""
    copies = gmsh.model.occ.copy([(3, v) for v in volumes])
    gmsh.model.occ.synchronize()
    new_volumes = [int(tag) for dim, tag in copies if dim == 3]
    new_boundary = _boundary(gmsh, new_volumes)
    old_faces = _surface_descriptors(gmsh, sorted(boundary))
    new_faces = _surface_descriptors(gmsh, sorted(new_boundary))
    mapping = {}
    for old in old_faces:
        matches = [new.tag for new in new_faces
                   if np.isclose(new.area_mm2, old.area_mm2, rtol=1e-9, atol=1e-7)
                   and np.allclose(new.centroid_mm, old.centroid_mm, rtol=0, atol=1e-7)
                   and np.allclose(new.bbox_mm, old.bbox_mm, rtol=0, atol=1e-7)]
        if len(matches) != 1:
            raise FemRegionMeshError(f"{_label(region)} could not detach its diaphragm mesh.")
        mapping[old.tag] = matches[0]
    if len(set(mapping.values())) != len(boundary):
        raise FemRegionMeshError(f"{_label(region)} has ambiguous copied boundary faces.")
    identity = np.eye(4).ravel().tolist()
    for group in region['surface_groups']:
        if group['kind'] == 'interface':
            name = physical_name(region['region_id'], group['group_id'], 'interface')
            for tag in sorted(groups[name]):
                gmsh.model.mesh.setPeriodic(2, [mapping[tag]], [tag], identity)
    return new_volumes, new_boundary, {name: {mapping[tag] for tag in tags}
                                       for name, tags in groups.items()}


def _msh(
    points: np.ndarray,
    groups: Mapping[str, np.ndarray],
    tetrahedra: np.ndarray | None = None,
    volume_name: str | None = None,
    *, scratch_dir: str | Path | None = None,
) -> str:
    """Write a compact tagged first-order mesh without remeshing any face."""
    import meshio

    used = np.unique(
        np.concatenate(
            [
                *(block.ravel() for block in groups.values()),
                *([tetrahedra.ravel()] if tetrahedra is not None else []),
            ]
        )
    )
    remap = np.full(len(points), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    cells, tags, names = [], [], {}
    for tag, (name, block) in enumerate(sorted(groups.items()), 1):
        cells.append(("triangle", remap[block]))
        tags.append(np.full(len(block), tag, dtype=np.int32))
        names[name] = np.array([tag, 2])
    if tetrahedra is not None:
        tag = len(groups) + 1
        cells.append(("tetra", remap[tetrahedra]))
        tags.append(np.full(len(tetrahedra), tag, dtype=np.int32))
        names[str(volume_name)] = np.array([tag, 3])
    mesh = meshio.Mesh(
        points[used],
        cells,
        field_data=names,
        cell_data={"gmsh:physical": tags, "gmsh:geometrical": tags},
    )
    with tempfile.TemporaryDirectory(prefix="wg-coupled-", dir=scratch_dir or temporary_directory_root()) as folder:
        path = Path(folder) / "mesh.msh"
        meshio.write(path, mesh, file_format="gmsh22", binary=False)
        return path.read_text(encoding="utf-8")


def read_coupled_exterior(msh_text: str) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Read the stored surface; boundary meanings travel in physical_names."""
    import meshio

    with tempfile.TemporaryDirectory(prefix="wg-coupled-read-", dir=temporary_directory_root()) as folder:
        path = Path(folder) / "exterior.msh"
        path.write_text(msh_text, encoding="utf-8")
        mesh = meshio.read(path, file_format="gmsh")
    names = {int(tag): name for name, (tag, dim) in mesh.field_data.items() if int(dim) == 2}
    groups: dict[str, list[np.ndarray]] = {}
    for block, tags in zip(mesh.cells, mesh.cell_data["gmsh:physical"]):
        if block.type != "triangle":
            raise FemRegionMeshError("Coupled exterior contains volume elements.")
        for tag in np.unique(tags):
            groups.setdefault(names[int(tag)], []).append(block.data[tags == tag])
    return np.asarray(mesh.points), {
        name: np.concatenate(blocks) for name, blocks in groups.items()
    }


def build_coupled_mesh(
    assembly_path: str | Path,
    regions: Sequence[tuple[str | Path, Mapping[str, Any]]],
    *,
    recipe: Mapping[str, Any],
    expected_geometry_hash: str,
    normalisation_matrix: Any,
    sizes: Mapping[str, Any],
    exterior_names: Mapping[str, Mapping[str, Any]],
    scratch_dir: str | Path | None = None,
    domain_planes: Sequence[str] = (), cut_planes: Sequence[str] = (),
    contacts_only: bool = False,
) -> dict[str, Any]:
    """Replay, fragment, mesh 2D once, then tetrahedralise only bounded air.

    All face matching happens before fragmentation in the export frame. Surface
    ancestry comes from BooleanFragments, so a split face retains its group.
    Assembly replay must match the exterior fingerprint and total transform.
    """
    import gmsh
    from hornlab_mesher.step_import import (
        anchor_surface_order,
        gmsh_surface_geometries,
        gmsh_surface_tags,
    )

    labels = "; ".join(
        _label(region, group)
        for _path, region in regions
        for group in region["surface_groups"]
        if group["kind"] == "interface"
    ) or "; ".join(_label(region) for _path, region in regions)
    try:
        gmsh.clear()
        gmsh.model.add("wg-coupled")
        bodies = declared_step_bodies(assembly_path)
        apply_occ_healing_options(
            gmsh,
            recipe.get("healing_options", ()),
            declared_solids=bodies["solid_breps"],
            void_solids=bodies["void_solids"],
            assembly_path=assembly_path,
        )
        assembly = _import_occ_root_bodies(gmsh, assembly_path)
        matrix = np.asarray(recipe.get("normalisation_matrix"), dtype=float)
        rigid_occ_motion(matrix)
        applied = apply_rigid_normalisation(gmsh, assembly, matrix)
        recentre = np.asarray(recipe.get("recentre_translation_mm", [0, 0, 0]), dtype=float)
        if recentre.shape != (3,) or not np.isfinite(recentre).all():
            raise FemRegionMeshError("normalisation mismatch: invalid exterior recentre recipe.")
        if recentre.any():
            gmsh.model.occ.translate(assembly, *recentre.tolist())
            gmsh.model.occ.synchronize()
        total = applied.copy()
        total[:3, 3] += recentre
        if not np.allclose(total, np.asarray(normalisation_matrix), atol=1e-9, rtol=0):
            raise FemRegionMeshError(
                "normalisation mismatch: exterior recipe and solver transform differ."
            )
        surfaces = gmsh_surface_tags()
        geometries = gmsh_surface_geometries(surfaces)
        if _geometry_fingerprint(geometries) != expected_geometry_hash:
            raise FemRegionMeshError(
                "normalisation mismatch: healed assembly differs from exterior mesh truth."
            )
        reference = [
            (tuple(center), float(area)) for center, area in recipe["surface_order_reference"]
        ]
        ordered = anchor_surface_order(surfaces, geometries, reference)
        source_surfaces = {
            name: [ordered[int(index)] for index in spec["indices"]]
            for name, spec in exterior_names.items()
        }
        region_data = []
        for path, region in regions:
            # Reset every healing bit: region STEP must remain one solid, as in F6.
            inventory = declared_step_bodies(path)
            apply_occ_healing_options(
                gmsh,
                (),
                declared_solids=inventory["solid_breps"],
                void_solids=inventory["void_solids"],
                assembly_path=path,
            )
            imported = gmsh.model.occ.importShapes(str(path))
            gmsh.model.occ.synchronize()
            volumes = [int(tag) for dim, tag in imported if dim == 3]
            if len(volumes) != 1:
                raise FemRegionMeshError(
                    f"{_label(region)} must be exactly one closed solid; its STEP holds {len(volumes)}."
                )
            boundary = sorted(_boundary(gmsh, volumes))
            assigned = assign_faces(region, _surface_descriptors(gmsh, boundary))
            # The SAME operations, in the SAME order, including recentring.
            region_applied = apply_rigid_normalisation(gmsh, [(3, volumes[0])], matrix)
            if recentre.any():
                gmsh.model.occ.translate([(3, volumes[0])], *recentre.tolist())
                gmsh.model.occ.synchronize()
            if not np.allclose(region_applied, applied, atol=1e-12, rtol=0):
                raise FemRegionMeshError(f"{_label(region)} normalisation mismatch.")
            verify_region_symmetry(gmsh, region, boundary, assigned, domain_planes, cut_planes)
            region_data.append((region, volumes, boundary, assigned))
        # Copy painted assembly faces as explicit tools. Passing constituent
        # faces alongside their parent volume yields empty ancestry for some
        # unchanged outer faces in OCC; independent copies retain that map.
        source_copies = {
            name: [
                int(copy_tag)
                for tag in tags
                for dim, copy_tag in gmsh.model.occ.copy([(2, tag)])
                if dim == 2
            ]
            for name, tags in source_surfaces.items()
        }
        objects = assembly
        tools = [
            (dim, tag)
            for _region, volumes, boundary, _assigned in region_data
            for dim, tags in ((3, volumes), (2, boundary))
            for tag in tags
        ]
        tools.extend((2, tag) for tags in source_copies.values() for tag in tags)
        descendants = fragment_fundamental_domain(gmsh, objects, tools, domain_planes)
        material_volumes = set().union(
            *(descendants[(3, tag)] for dim, tag in assembly if dim == 3)
        )
        free_surfaces = set().union(*(descendants[(2, tag)] for dim, tag in assembly if dim == 2))
        exterior_surfaces = _boundary(gmsh, sorted(material_volumes)) | free_surfaces
        # Drop internal faces of the material + air union.
        exterior_surfaces = {
            tag for tag in exterior_surfaces if len(gmsh.model.getAdjacencies(2, tag)[0]) < 2
        }
        source_descendants = {
            name: set().union(*(descendants[(2, tag)] for tag in tags))
            for name, tags in source_copies.items()
        }
        exterior_groups: dict[str, set[int]] = {}
        kinds: dict[str, dict[str, str]] = {}
        prepared = []
        sizing = []
        air_owners: dict[int, str] = {}
        region_names = {
            region["region_id"]: str(region.get("name", region["region_id"]))
            for region, *_rest in region_data
        }
        moving_claims: dict[str, dict[int, Mapping[str, Any]]] = {}
        touching_regions: set[str] = set()
        port_sizes = []
        region_volumes = []
        for region, original_volumes, _boundary_tags, assigned in region_data:
            volumes = sorted(set().union(*(descendants[(3, tag)] for tag in original_volumes)))
            if not volumes or material_volumes.intersection(volumes):
                raise FemRegionMeshError(
                    f"{_label(region)} overlaps assembly material or lost its volume."
                )
            for volume in volumes:
                if volume in air_owners:
                    raise FemRegionMeshError(
                        f"{_label(region)} overlaps FEM region {region_names[air_owners[volume]]!r}."
                    )
                air_owners[volume] = region["region_id"]
            groups = {
                physical_name(region["region_id"], group["group_id"], group["kind"]): set().union(
                    *(descendants[(2, tag)] for tag in assigned[group["group_id"]])
                )
                for group in region["surface_groups"]
            }
            moving_claims[region["region_id"]] = {
                tag: group
                for group in region["surface_groups"] if group["kind"] == "moving"
                for tag in groups[physical_name(region["region_id"], group["group_id"], "moving")]
            }
            region_volumes.append((region, volumes, groups))
        # Check all regions before assigning implicit walls or interfaces.
        # A shared air/air face is neither a wall nor an exterior interface.
        for tag in _boundary(gmsh, sorted(air_owners)):
            adjacent = [int(v) for v in gmsh.model.getAdjacencies(2, tag)[0] if int(v) in air_owners]
            owners = list(dict.fromkeys(air_owners[v] for v in adjacent))
            if len(adjacent) > 1 and len(owners) == 1:
                raise FemRegionMeshError(
                    f"FEM region {region_names[owners[0]]!r} is cut into pieces by another body; "
                    "make the region one closed solid"
                )
            if len(owners) > 1:
                first, second = owners[:2]
                a, b = moving_claims[first].get(tag), moving_claims[second].get(tag)
                if (len(owners) == 2 and a and b and a.get('drive_channel_id')
                        and a.get('drive_channel_id') == b.get('drive_channel_id')):
                    # Intake has no driver spec. Solve admission must require
                    # an electrodynamic component for this multi-region channel.
                    touching_regions.update(owners)
                    continue
                raise FemRegionMeshError(
                    f"FEM regions {region_names[first]!r} and {region_names[second]!r} touch at boundary "
                    f"face {tag}; shared FEM air boundaries require moving faces on both sides "
                    "of the same electrodynamic channel."
                )
        if contacts_only:
            # Interior-only regions still need the same overlap/contact gate,
            # then each region is meshed by its independent FEM child.
            return {"exterior": None, "regions": {}}
        for region, volumes, groups in region_volumes:
            boundary = _boundary(gmsh, volumes)
            caps = symmetry_surfaces(gmsh, sorted(boundary), domain_planes)
            if any(caps & tags for tags in groups.values()):
                raise FemRegionMeshError(f"{_label(region)} declares a boundary on a symmetry plane")
            moving = set().union(*(
                groups[physical_name(region["region_id"], g["group_id"], g["kind"])]
                for g in region["surface_groups"] if g["kind"] == "moving"
            ))
            for source, faces in source_descendants.items():
                covered = faces & boundary
                if not covered:
                    continue
                if covered - moving:
                    raise FemRegionMeshError(
                        f"Exterior source {source!r} is covered by {_label(region)} on faces "
                        "not claimed by its moving group; unpaint or correct the source and diaphragm."
                    )
                raise FemRegionMeshError(
                    f"Exterior source {source!r} is covered by {_label(region)}'s moving group; "
                    "unsupported in 0.3.6: unpaint the source; the region's diaphragm group drives it."
                )
            if (not domain_planes and region['region_id'] not in touching_regions
                    and not any(g["kind"] == "interface" for g in region["surface_groups"])):
                continue
            claimed = set().union(*groups.values())
            if caps:
                groups[physical_name(region["region_id"], SYMMETRY_GROUP_ID, "rigid")] = caps
            walls = boundary - claimed - caps
            if walls:
                groups.setdefault(
                    physical_name(region["region_id"], WALLS_GROUP_ID, "rigid"), set()
                ).update(walls)
            for group in region["surface_groups"]:
                name = physical_name(region["region_id"], group["group_id"], group["kind"])
                tags = groups[name]
                if not tags or not tags <= boundary:
                    raise FemRegionMeshError(
                        f"{_label(region, group)} did not survive as a shared OCC surface."
                    )
                if group["kind"] == "interface":
                    if tags & free_surfaces:
                        sheet = min(tags & free_surfaces)
                        raise FemRegionMeshError(
                            f"Assembly rigid sheet face {sheet} coincides with "
                            f"{_label(region, group)} interface; remove the sheet or correct the interface."
                        )
                    # A buried face is not an exterior interface. The air skin
                    # provides the shared surface; closure proves the rim joins.
                    if any(len(gmsh.model.getAdjacencies(2, tag)[0]) != 1 for tag in tags):
                        raise FemRegionMeshError(
                            f"{_label(region, group)} interface is not shared with the exterior air skin (buried face)."
                        )
                    if any(tags & existing for existing in exterior_groups.values()):
                        raise FemRegionMeshError(
                            f"{_label(region, group)} interface is claimed twice on the exterior."
                        )
                    exterior_surfaces.update(tags)
                    exterior_groups[name] = tags
                    kinds[name] = {"kind": "interface"}
            # A naked undeclared air face must not silently become an exterior wall.
            interfaces = set().union(
                *(
                    groups[physical_name(region["region_id"], g["group_id"], g["kind"])]
                    for g in region["surface_groups"]
                    if g["kind"] == "interface"
                )
            )
            # Author-cut exteriors are open sheet roots. A sheet shared with
            # a non-interface air boundary is a chamber wall, not a second
            # BEM face. Volume roots already exclude it through adjacency.
            if domain_planes:
                exterior_surfaces -= (boundary - interfaces) & free_surfaces
            naked = {
                tag
                for tag in boundary - interfaces - caps
                if len(gmsh.model.getAdjacencies(2, tag)[0]) == 1
                and tag not in exterior_surfaces
                and not (domain_planes and tag in free_surfaces)
            }
            if naked and interfaces:
                raise FemRegionMeshError(
                    f"{_label(region)} has uncovered air boundary faces; the exterior is not closed by declared interfaces."
                )
            prepared.append((region, volumes, boundary, groups))
        detached = []
        for region, volumes, boundary, groups in prepared:
            if region['region_id'] in touching_regions:
                for group in region['surface_groups']:
                    if group['kind'] == 'interface':
                        tags = groups[physical_name(region['region_id'], group['group_id'], 'interface')]
                        port_sizes.append((tags, min(float(group['mesh_size_mm']), float(region['mesh_size_mm']))))
                volumes, boundary, groups = _detach_region(gmsh, region, volumes, boundary, groups)
            detached.append((region, volumes, boundary, groups))
            for group in region['surface_groups']:
                tags = groups[physical_name(region['region_id'], group['group_id'], group['kind'])]
                sizing.append((tags, float(group['mesh_size_mm']), float(region['mesh_size_mm']),
                               boundary, volumes))
        prepared = detached
        for name, faces in source_descendants.items():
            selected = faces & exterior_surfaces
            if not selected:
                raise FemRegionMeshError(
                    f"Exterior source {name!r} disappeared from the coupled skin."
                )
            exterior_groups[name] = selected
            kinds[name] = dict(exterior_names[name]["kind"])
        exterior_surfaces -= symmetry_surfaces(gmsh, sorted(exterior_surfaces), domain_planes)
        claimed_exterior = set().union(*exterior_groups.values())
        rigid = exterior_surfaces - claimed_exterior
        if rigid:
            exterior_groups["wg-coupled-v1|rigid"] = rigid
            kinds["wg-coupled-v1|rigid"] = {"kind": "rigid"}

        all_sizes = [
            float(sizes["rigid_size_mm"]),
            *map(float, sizes["source_size_mm"].values()),
            *(float(region["mesh_size_mm"]) for region, *_rest in prepared),
            *(size for _tags, size, _base, _boundary_tags, _volumes in sizing),
        ]
        gmsh.option.setNumber("Mesh.MeshSizeMin", min(all_sizes))
        gmsh.option.setNumber("Mesh.MeshSizeMax", max(all_sizes))
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        fields = []

        def constant(size, surfaces, volumes=()):
            value = gmsh.model.mesh.field.add("MathEval")
            gmsh.model.mesh.field.setString(value, "F", str(size))
            restrict = gmsh.model.mesh.field.add("Restrict")
            gmsh.model.mesh.field.setNumber(restrict, "InField", value)
            gmsh.model.mesh.field.setNumbers(restrict, "SurfacesList", sorted(surfaces))
            gmsh.model.mesh.field.setNumbers(restrict, "VolumesList", list(volumes))
            fields.append(restrict)

        constant(float(sizes["rigid_size_mm"]), exterior_surfaces)
        for name, spec in exterior_names.items():
            constant(float(spec["size_mm"]), exterior_groups[name])
        for region, volumes, boundary, _groups in prepared:
            constant(float(region["mesh_size_mm"]), boundary, volumes)
        for tags, size in port_sizes:
            constant(size, tags)
        for tags, size, base, boundary, volumes in sizing:
            if size >= base:
                continue
            distance = gmsh.model.mesh.field.add("Distance")
            gmsh.model.mesh.field.setNumbers(distance, "SurfacesList", sorted(tags))
            gmsh.model.mesh.field.setNumber(distance, "Sampling", 60)
            threshold = gmsh.model.mesh.field.add("Threshold")
            for key, value in {
                "InField": distance,
                "SizeMin": size,
                "SizeMax": base,
                "DistMin": 0,
                "DistMax": SIZE_BLEND_FACTOR * base,
                "StopAtDistMax": 1,
            }.items():
                gmsh.model.mesh.field.setNumber(threshold, key, value)
            if not touching_regions:
                fields.append(threshold)
                continue
            restrict = gmsh.model.mesh.field.add('Restrict')
            gmsh.model.mesh.field.setNumber(restrict, 'InField', threshold)
            gmsh.model.mesh.field.setNumbers(restrict, 'SurfacesList', sorted(boundary))
            gmsh.model.mesh.field.setNumbers(restrict, 'VolumesList', volumes)
            fields.append(restrict)
        minimum = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(minimum, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(minimum)
        gmsh.option.setNumber("Mesh.MeshOnlyVisible", 0)
        gmsh.model.mesh.generate(2)
        # Existing 2D elements remain: do not regenerate an interface separately.
        gmsh.model.setVisibility([(3, tag) for tag in material_volumes], 0)
        meshed_air = {v for _region, volumes, *_rest in prepared for v in volumes}
        gmsh.model.setVisibility([(3, v) for v in air_owners if v not in meshed_air], 0)
        gmsh.model.setVisibility([(3, v) for v in meshed_air], 1)
        gmsh.option.setNumber("Mesh.MeshOnlyVisible", 1)
        gmsh.model.mesh.generate(3)
        if any(
            len(tags)
            for volume in material_volumes
            for tags in gmsh.model.mesh.getElements(3, volume)[1]
        ):
            raise FemRegionMeshError("Coupled mesh unexpectedly tetrahedralised assembly material.")
        node_tags, coordinates, _parametric = gmsh.model.mesh.getNodes()
        points = np.asarray(coordinates).reshape(-1, 3)
        remap = {int(tag): index for index, tag in enumerate(node_tags)}
        exterior_blocks = {
            name: _elements(gmsh, 2, sorted(tags), remap) for name, tags in exterior_groups.items()
        }
        ordered_names = sorted(exterior_blocks)
        oriented = _closed(
            points, np.concatenate([exterior_blocks[name] for name in ordered_names]), domain_planes
        )
        offset = 0
        for name in ordered_names:
            length = len(exterior_blocks[name])
            exterior_blocks[name] = oriented[offset : offset + length]
            offset += length
        built_regions = {}
        for region, volumes, boundary, groups in prepared:
            tetrahedra = _elements(gmsh, 3, volumes, remap)
            if not len(tetrahedra):
                raise FemRegionMeshError(f"{_label(region)} produced no tetrahedra.")
            blocks = {
                name: _outward_facets(points, _elements(gmsh, 2, sorted(tags), remap), tetrahedra)
                for name, tags in groups.items()
            }
            specs = {
                physical_name(region["region_id"], g["group_id"], g["kind"]): g
                for g in region["surface_groups"]
            }
            names = {}
            stats = {}
            for tag, (name, block) in enumerate(sorted(blocks.items()), 1):
                spec = specs.get(name, {"group_id": (SYMMETRY_GROUP_ID if "group=_symmetry|" in name
                                                              else WALLS_GROUP_ID), "kind": "rigid"})
                if not len(block):
                    raise FemRegionMeshError(f"{_label(region, spec)} retained no boundary triangles.")
                names[name] = {
                    "dimension": 2,
                    "tag": tag,
                    "entities": sorted(groups[name]),
                    "group_id": spec["group_id"],
                    "kind": spec["kind"],
                    **(
                        {"drive_channel_id": spec["drive_channel_id"]}
                        if spec.get("drive_channel_id")
                        else {}
                    ),
                }
                corners = points[block]
                area = (
                    np.linalg.norm(
                        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]),
                        axis=1,
                    ).sum()
                    / 2
                )
                stats[spec["group_id"]] = {
                    "kind": spec["kind"],
                    "area_mm2": float(area),
                    "face_count": len(groups[name]),
                    "triangle_count": len(block),
                }
                if spec["kind"] == "interface":
                    _check_interface(points, block, exterior_blocks[name], _label(region, spec))
            volume_name = physical_name(region["region_id"])
            names[volume_name] = {"dimension": 3, "tag": len(blocks) + 1, "entities": volumes}
            text = _msh(points, blocks, tetrahedra, volume_name, scratch_dir=scratch_dir)
            domain = read_fem_region_domain(text, region["region_id"], scratch_dir=scratch_dir)
            built_regions[region["region_id"]] = {
                "region_id": region["region_id"],
                "msh_text": text,
                "physical_names": names,
                "normalisation_matrix": total.tolist(),
                "stats": {
                    "tetrahedra": len(tetrahedra),
                    "nodes": len(domain.points_mm),
                    "boundary_surfaces": len(boundary),
                    "wall_surfaces": len(
                        groups.get(physical_name(region["region_id"], WALLS_GROUP_ID, "rigid"), ())
                    ),
                    "surface_groups": stats,
                },
            }
        return {
            "exterior": {
                "msh_text": _msh(points, exterior_blocks, scratch_dir=scratch_dir),
                "physical_names": {
                    name: {
                        **kinds[name],
                        "dimension": 2,
                        "tag": index,
                        "entities": sorted(exterior_groups[name]),
                    }
                    for index, name in enumerate(sorted(exterior_blocks), 1)
                },
                "normalisation_matrix": total.tolist(),
                "stats": {"triangles": len(oriented), "open_edges": open_edges(oriented),
                          "domain_planes": list(domain_planes)},
            },
            "regions": built_regions,
        }
    except FemRegionMeshError as exc:
        raise FemRegionMeshError(f"{labels}: {exc}", symmetry_plane=exc.symmetry_plane) from exc
    except Exception as exc:
        raise FemRegionMeshError(f"{labels}: coupled meshing failed: {exc}") from exc
    finally:
        gmsh.option.setNumber("Mesh.MeshOnlyVisible", 0)
        gmsh.clear()
