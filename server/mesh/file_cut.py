"""Triangle/OCC adaptation to the same symmetry cutter used for CAD returns.

Called only in an owned Gmsh session. Original arrays are read-only inputs;
native geometry and generated children are disposable until verification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

import numpy as np

if TYPE_CHECKING:
    from server.mesh.file_import import FileMesh

# A cut optimisation bound, independent of import and solver admission limits.
MAX_FILE_CUT_TRIANGLES = 5_000


def cut_file_mesh(mesh: FileMesh, planes: tuple[str, ...], roles: Mapping[int, str]) -> tuple[FileMesh, dict[str, Any]]:
    if len(mesh.triangles) > MAX_FILE_CUT_TRIANGLES:
        return mesh, {"cut_planes": [], "note": (
            f"This mesh has {len(mesh.triangles):,} triangles; cutting is available up to "
            f"{MAX_FILE_CUT_TRIANGLES:,}. Export the half or quarter to solve faster."
        )}

    import gmsh
    from hornlab_mesher.step_prepare import TriangleCutRefused, auto_cut_occ_geometry

    previous = gmsh.model.getCurrent()
    gmsh.model.add("file-symmetry-cut")
    try:
        faces, groups = _load_model(mesh, roles)
        try:
            cut = auto_cut_occ_geometry(groups, planes=planes, triangle_surface_mesh=True)
            report = dict(cut.report)
            if not cut.planes:
                report["note"] = "The mesh or its source groups do not mirror across the requested planes. Export the half or quarter to solve faster."
                return mesh, report
            result = _mesh_children(mesh, faces, cut)
            if set(roles) - set(result.tags.tolist()):
                raise TriangleCutRefused("the cut removed a driver or port group")
            rejected = set(planes) - set(cut.planes)
            if rejected:
                report["note"] = "Only some symmetry planes could be verified. Export the half or quarter to solve faster."
            return result, report
        except TriangleCutRefused:
            # Refusal can follow a destructive boolean. Reload the original
            # model before any fallback, even though its immutable arrays are
            # already the solver mesh and need no native remeshing.
            gmsh.model.remove()
            gmsh.model.add("file-symmetry-cut")
            _load_model(mesh, roles)
            return mesh, {"cut_planes": [], "note": "The mesh could not be cut safely. Export the half or quarter to solve faster."}
    finally:
        gmsh.model.remove()
        if previous:
            gmsh.model.setCurrent(previous)


def _load_model(mesh: FileMesh, roles: Mapping[int, str]) -> tuple[list[int], list[Any]]:
    import gmsh
    from hornlab_mesher.step_prepare import OccSurfaceGroup, OccSurfaceRole, OccSurfaceSelector

    occ = gmsh.model.occ
    vertices = [occ.addPoint(*point) for point in mesh.points]
    edges: dict[tuple[int, int], int] = {}
    faces = []
    for triangle in mesh.triangles:
        lines = []
        for a, b in zip(triangle, np.roll(triangle, -1)):
            key = (int(min(a, b)), int(max(a, b)))
            if key not in edges:
                edges[key] = occ.addLine(vertices[key[0]], vertices[key[1]])
            lines.append(edges[key] if a < b else -edges[key])
        faces.append(occ.addPlaneSurface([occ.addCurveLoop(lines)]))
    occ.synchronize()
    groups = [OccSurfaceGroup(
        f"group_{tag}", OccSurfaceSelector(face for face, owner in zip(faces, mesh.tags) if owner == tag),
        # Like CAD, two independent sources sharing HF are not interchangeable.
        OccSurfaceRole(f"group_{tag}" if tag in roles else "rigid"),
    ) for tag in np.unique(mesh.tags)]
    return faces, groups


def _mesh_children(mesh: FileMesh, faces: list[int], cut: Any) -> FileMesh:
    import gmsh
    from hornlab_mesher.mesh_repair_ops import _weld_near_duplicate_vertices
    from hornlab_mesher.step_prepare import TriangleCutRefused
    from server.mesh.file_import import FileMesh

    # No mesh-size policy is applied to the file: retain each untouched input
    # facet, and triangulate only OCC's clipped polygonal children.
    span = float(np.ptp(mesh.points, axis=0).max())
    options = {"Mesh.MeshSizeMin": span * 10, "Mesh.MeshSizeMax": span * 10,
               "Mesh.MeshSizeFromPoints": 0, "Mesh.MeshSizeFromCurvature": 0,
               "Mesh.MeshSizeExtendFromBoundary": 0, "Mesh.Algorithm": 1,
               "Mesh.ElementOrder": 1, "Mesh.Optimize": 0}
    saved = {key: gmsh.option.getNumber(key) for key in options}
    try:
        for key, value in options.items():
            gmsh.option.setNumber(key, value)
        for _, curve in gmsh.model.getEntities(1):
            gmsh.model.mesh.setTransfiniteCurve(curve, 2)
        gmsh.model.mesh.generate(2)
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        xyz = np.asarray(coordinates).reshape(-1, 3)
        indices = {int(tag): i + len(mesh.points) for i, tag in enumerate(node_tags)}
        points = np.concatenate((mesh.points, xyz))
        triangles, tags, unchanged = [], [], []
        axes = [{"x0": 0, "y0": 1}[plane] for plane in cut.planes]
        tolerance = max(span * 1e-10, np.max(np.abs(points)) * np.finfo(float).eps * 64)
        for i, parent in enumerate(faces):
            children = cut.parent_to_children.get(parent, ())
            original = mesh.points[mesh.triangles[i]]
            if children and np.all(original[:, axes] >= 0):
                unchanged.append(len(triangles))
                triangles.append(mesh.triangles[i].tolist())
                tags.append(int(mesh.tags[i]))
                continue
            normal = np.cross(original[1] - original[0], original[2] - original[0])
            for child in children:
                types, _, connectivity = gmsh.model.mesh.getElements(2, child)
                local = []
                for kind, nodes in zip(types, connectivity):
                    if kind != 2:
                        raise TriangleCutRefused("the cut generated nontriangle elements")
                    local.extend([[indices[int(node)] for node in row] for row in np.asarray(nodes).reshape(-1, 3)])
                if not local:
                    raise TriangleCutRefused("a retained child has no triangles")
                array = np.asarray(local)
                corners = points[array]
                vectors = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
                area = float(np.linalg.norm(vectors, axis=1).sum() / 2)
                expected = float(gmsh.model.occ.getMass(2, child))
                basis = np.stack((original[1] - original[0], original[2] - original[0]), axis=1)
                uv = (corners - original[0]) @ np.linalg.pinv(basis).T
                if (not np.isfinite(corners).all() or abs(area - expected) > max(expected * 1e-7, tolerance**2)
                        or np.max(np.abs((corners - original[0]) @ normal)) > tolerance * np.linalg.norm(normal)
                        or uv.min() < -1e-7 or uv.sum(axis=2).max() > 1 + 1e-7):
                    raise TriangleCutRefused("the child triangles do not cover their original facet")
                reverse = vectors @ normal < 0
                array[reverse] = array[reverse][:, [0, 2, 1]]
                triangles.extend(array.tolist())
                tags.extend([int(mesh.tags[i])] * len(array))
        if not triangles:
            raise TriangleCutRefused("the cut retained no triangles")
        array = np.asarray(triangles, dtype=np.int64)
        welded = _weld_near_duplicate_vertices(points * 1000, array, tol_mm=tolerance * 1000)
        if unchanged and not np.array_equal(points[welded[unchanged]], points[array[unchanged]]):
            raise TriangleCutRefused("welding would move an unchanged input facet")
        used, compact = np.unique(welded, return_inverse=True)
        return FileMesh(points[used], compact.reshape(-1, 3), np.asarray(tags), dict(mesh.names))
    finally:
        for key, value in saved.items():
            gmsh.option.setNumber(key, value)
