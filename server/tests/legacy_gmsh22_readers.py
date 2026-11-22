"""Frozen copies of the seven Gmsh 2.2 readers WG used before ``server.mesh.gmsh22``.

Test oracle only: ``test_gmsh22_reader.py`` runs every fixture through these and
through the shared reader's views and requires identical answers. Copied
verbatim from e48bfaeea; nothing in ``server`` imports this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from server.solver.beat_adapter.mesh import SurfaceMesh
from server.solver.ground_plane import GroundPlane, GroundPlaneClearance

def _project_to_symmetry(vector: np.ndarray, plane: str | None) -> np.ndarray:
    projected = np.asarray(vector, dtype=float).copy()
    if plane == "yz":
        projected[0] = 0.0
    elif plane == "xz":
        projected[1] = 0.0
    elif plane == "yz+xz":
        projected[:2] = 0.0
    return projected


@dataclass(frozen=True)
class _ObservationFrameParts:
    axis: np.ndarray
    mouth_center: np.ndarray
    source_center: np.ndarray
    horizontal: np.ndarray
    vertical: np.ndarray


def _gmsh22_observation_frame_parts(
    msh_text: str,
    *,
    symmetry_plane: str | None,
    aperture_tag: int | None = None,
) -> _ObservationFrameParts:
    """Read the authoritative observation basis and centres from a Gmsh artifact."""

    lines = msh_text.splitlines()
    try:
        nodes_start = lines.index("$Nodes")
        elements_start = lines.index("$Elements")
        node_count = int(lines[nodes_start + 1])
        element_count = int(lines[elements_start + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(
            "observation framing requires an ASCII Gmsh 2.2 solver artifact"
        ) from exc
    node_rows = lines[nodes_start + 2 : nodes_start + 2 + node_count]
    nodes: dict[int, np.ndarray] = {}
    try:
        for row in node_rows:
            parts = row.split()
            if len(parts) >= 4:
                nodes[int(parts[0])] = np.asarray(parts[1:4], dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("solver artifact contains invalid Gmsh nodes") from exc

    tagged_triangles: dict[int, list[list[np.ndarray]]] = {}
    try:
        for row in lines[elements_start + 2 : elements_start + 2 + element_count]:
            parts = row.split()
            if len(parts) < 3 or int(parts[1]) != 2:
                continue
            tag_count = int(parts[2])
            first_node = 3 + tag_count
            if tag_count < 1 or len(parts) < first_node + 3:
                continue
            physical_tag = int(parts[3])
            node_ids = [int(value) for value in parts[first_node : first_node + 3]]
            if all(node_id in nodes for node_id in node_ids):
                tagged_triangles.setdefault(physical_tag, []).append(
                    [nodes[node_id] for node_id in node_ids]
                )
    except (TypeError, ValueError) as exc:
        raise ValueError("solver artifact contains invalid Gmsh elements") from exc

    source_triangles = tagged_triangles.get(2, [])
    if not nodes or not source_triangles:
        raise ValueError(
            "observation framing requires source-tagged triangles (physical tag 2)"
        )
    vertices = np.stack(list(nodes.values()))
    source = np.asarray(source_triangles, dtype=float)
    normals = np.cross(source[:, 1] - source[:, 0], source[:, 2] - source[:, 0])
    areas = np.linalg.norm(normals, axis=1)
    valid = areas > 1.0e-15
    if not np.any(valid):
        raise ValueError("source-tagged triangles cannot define an observation axis")
    normals, areas, source = normals[valid], areas[valid], source[valid]
    reference = normals[int(np.argmax(areas))]
    signs = np.sign(normals @ reference)
    signs[signs == 0.0] = 1.0
    axis = np.sum(normals * signs[:, None], axis=0)
    axis /= np.linalg.norm(axis)
    projected_axis = _project_to_symmetry(axis, symmetry_plane)
    if np.linalg.norm(projected_axis) > 1.0e-12:
        axis = projected_axis / np.linalg.norm(projected_axis)
    centroids = np.mean(source, axis=1)
    source_center = np.average(centroids, weights=areas, axis=0)

    if aperture_tag is None:
        along_axis = vertices @ axis
        threshold = float(np.max(along_axis) - 0.02 * np.ptp(along_axis))
        mouth_center = np.mean(vertices[along_axis >= threshold], axis=0)
    else:
        aperture = np.asarray(tagged_triangles.get(int(aperture_tag), []), dtype=float)
        if not aperture.size:
            raise ValueError(
                f"solver artifact has no aperture triangles with physical tag {aperture_tag}"
            )
        aperture_normals = np.cross(
            aperture[:, 1] - aperture[:, 0], aperture[:, 2] - aperture[:, 0]
        )
        aperture_areas = np.linalg.norm(aperture_normals, axis=1)
        aperture_valid = aperture_areas > 1.0e-15
        if not np.any(aperture_valid):
            raise ValueError(
                f"aperture triangles with physical tag {aperture_tag} cannot define an origin"
            )
        aperture_centroids = np.mean(aperture[aperture_valid], axis=1)
        mouth_center = np.average(
            aperture_centroids,
            weights=aperture_areas[aperture_valid],
            axis=0,
        )

    reference_axis = np.asarray([1.0, 0.0, 0.0])
    if abs(float(axis @ reference_axis)) >= 0.9:
        reference_axis = np.asarray([0.0, 1.0, 0.0])
    horizontal = reference_axis - float(reference_axis @ axis) * axis
    horizontal /= np.linalg.norm(horizontal)
    vertical = np.cross(axis, horizontal)
    return _ObservationFrameParts(
        axis=axis,
        mouth_center=mouth_center,
        source_center=source_center,
        horizontal=horizontal,
        vertical=vertical,
    )


def mesh_trace_dof_counts(
    msh_text: str,
    mesh_stats: Mapping[str, Any] | None = None,
) -> tuple[int, int]:
    """Estimate P1 and DP0 counts from a Gmsh 2.2 mesh, with stats fallback."""

    try:
        lines = msh_text.splitlines()
        start = lines.index("$Elements")
        element_count = int(lines[start + 1].strip())
        element_lines = lines[start + 2 : start + 2 + element_count]
        if len(element_lines) != element_count:
            raise ValueError("truncated element section")
        used_vertices: set[int] = set()
        triangle_count = 0
        for line in element_lines:
            fields = line.split()
            if len(fields) < 4 or int(fields[1]) != 2:
                continue
            tag_count = int(fields[2])
            node_start = 3 + tag_count
            nodes = fields[node_start:]
            if len(nodes) != 3:
                raise ValueError("invalid first-order triangle")
            used_vertices.update(int(value) for value in nodes)
            triangle_count += 1
        if used_vertices and triangle_count:
            return len(used_vertices), triangle_count
    except (IndexError, TypeError, ValueError):
        pass

    stats = mesh_stats if isinstance(mesh_stats, Mapping) else {}
    try:
        vertex_count = int(stats["vertex_count"])
        triangle_count = int(stats["triangle_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("mesh does not expose P1/DP0 trace dimensions") from exc
    if vertex_count <= 0 or triangle_count <= 0:
        raise ValueError("mesh trace dimensions must be positive")
    return vertex_count, triangle_count


def read_surface(msh_text: str, *, scale_to_m: float = 1.0) -> SurfaceMesh:
    """Retain node rows, triangle rows, winding and physical tags exactly."""
    if not np.isfinite(scale_to_m) or scale_to_m <= 0:
        raise ValueError("Mesh scale must be finite and positive")
    lines = [line.strip() for line in msh_text.splitlines()]
    try:
        header = lines.index("$MeshFormat")
        if lines[header + 1].split() != ["2.2", "0", "8"]:
            raise ValueError("Expected ASCII Gmsh 2.2")
        start = lines.index("$Nodes") + 1
        count = int(lines[start])
        if count <= 0 or lines[start + count + 1] != "$EndNodes":
            raise ValueError("Invalid Gmsh nodes")
        nodes = [row.split() for row in lines[start + 1:start + count + 1]]
        if any(len(row) != 4 for row in nodes):
            raise ValueError("Invalid Gmsh node row")
        node_ids = tuple(int(row[0]) for row in nodes)
        if min(node_ids) <= 0 or len(set(node_ids)) != count:
            raise ValueError("Invalid or duplicate Gmsh node IDs")
        points = np.asarray([row[1:] for row in nodes], dtype=float) * scale_to_m
        lookup = {node_id: i for i, node_id in enumerate(node_ids)}
        start = lines.index("$Elements") + 1
        count = int(lines[start])
        if count <= 0 or lines[start + count + 1] != "$EndElements":
            raise ValueError("Invalid Gmsh elements")
        faces, tags, element_ids = [], [], []
        for line in lines[start + 1:start + count + 1]:
            row = [int(value) for value in line.split()]
            if len(row) < 3:
                raise ValueError("Invalid Gmsh element row")
            if row[1] not in {1, 2, 15}:
                raise ValueError(f"Unsupported Gmsh element type {row[1]}; expected points (15), lines (1) or triangles (2)")
            if row[1] != 2:
                continue  # Physical lines/points do not enter the BEM surface.
            n_tags = row[2]
            if n_tags < 1 or len(row) != 6 + n_tags or row[3] <= 0:
                raise ValueError("Triangle requires a positive physical tag")
            faces.append([lookup[node] for node in row[3 + n_tags:]])
            tags.append(row[3])
            element_ids.append(row[0])
        mesh = SurfaceMesh(points, np.asarray(faces, dtype=np.int64),
                           np.asarray(tags, dtype=np.int64), node_ids, tuple(element_ids))
        if (not faces or not np.isfinite(points).all() or len(set(element_ids)) != len(faces)
                or min(element_ids) <= 0 or np.any(mesh.areas_m2 <= 0)):
            raise ValueError("Surface requires finite nodes and unique nondegenerate triangles")
        return mesh
    except (IndexError, KeyError, OverflowError) as exc:
        raise ValueError("Invalid ASCII Gmsh 2.2 surface") from exc


def scale_msh_text(msh_text: str, scale_to_m: float) -> str:
    """Scale node coordinates while preserving tags, IDs and element rows."""
    mesh = read_surface(msh_text, scale_to_m=scale_to_m)
    lines = msh_text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == "$Nodes") + 2
    for index, (node_id, point) in enumerate(zip(mesh.node_ids, mesh.points_m, strict=True)):
        lines[start + index] = f"{node_id} " + " ".join(format(float(value), ".17g") for value in point)
    return "\n".join(lines) + "\n"


def _msh_triangles(msh_text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Node coordinates, triangle corner rows and physical tags of a Gmsh 2.2 mesh."""

    lines = msh_text.splitlines()
    try:
        nodes_start = lines.index("$Nodes")
        elements_start = lines.index("$Elements")
        node_count = int(lines[nodes_start + 1])
        element_count = int(lines[elements_start + 1])
        index: dict[str, int] = {}
        coordinates = np.empty((node_count, 3), dtype=float)
        for row, line in enumerate(lines[nodes_start + 2 : nodes_start + 2 + node_count]):
            parts = line.split()
            index[parts[0]] = row
            coordinates[row] = [float(value) for value in parts[1:4]]
        corners: list[list[int]] = []
        tags: list[int] = []
        for line in lines[elements_start + 2 : elements_start + 2 + element_count]:
            parts = line.split()
            if len(parts) < 4 or parts[1] != "2":
                continue
            tag_count = int(parts[2])
            if tag_count < 1 or len(parts) < 6 + tag_count:
                continue
            corners.append([index[node] for node in parts[3 + tag_count : 6 + tag_count]])
            tags.append(int(parts[3]))
    except (ValueError, IndexError, KeyError) as exc:
        raise ValueError("imported mesh artifact is not a readable Gmsh 2.2 mesh") from exc
    return (
        coordinates,
        np.asarray(corners, dtype=np.int64).reshape(-1, 3),
        np.asarray(tags, dtype=np.int64),
    )


class TaggedMeshError(ValueError):
    pass


def parse_tagged_msh(msh_text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[int, str]]:
    """Points, triangles, per-triangle physical tags and physical names of an ASCII MSH 2.2."""

    lines = [line.strip() for line in msh_text.splitlines()]
    try:
        version = lines[lines.index("$MeshFormat") + 1].split()
    except (ValueError, IndexError) as exc:
        raise TaggedMeshError("the mesh artifact is not an ASCII Gmsh 2.2 mesh") from exc
    if len(version) < 2 or not version[0].startswith("2.") or version[1] != "0":
        raise TaggedMeshError("the mesh artifact is not an ASCII Gmsh 2.2 mesh")
    names: dict[int, str] = {}
    if "$PhysicalNames" in lines:
        start = lines.index("$PhysicalNames")
        for row in lines[start + 2 : start + 2 + int(lines[start + 1])]:
            parts = row.split(maxsplit=2)
            if len(parts) == 3 and parts[0] == "2":
                names[int(parts[1])] = parts[2].strip('"')
    try:
        nodes_start = lines.index("$Nodes")
        count = int(lines[nodes_start + 1])
        rows = [line.split() for line in lines[nodes_start + 2 : nodes_start + 2 + count]]
        index = {row[0]: position for position, row in enumerate(rows)}
        points = np.asarray([row[1:4] for row in rows], dtype=float).reshape(-1, 3)
        elements_start = lines.index("$Elements")
        element_count = int(lines[elements_start + 1])
        corners: list[list[int]] = []
        tags: list[int] = []
        for line in lines[elements_start + 2 : elements_start + 2 + element_count]:
            parts = line.split()
            if len(parts) < 4 or parts[1] != "2":
                continue
            tag_count = int(parts[2])
            corners.append([index[node] for node in parts[3 + tag_count : 6 + tag_count]])
            tags.append(int(parts[3]) if tag_count >= 1 else 0)
    except (ValueError, IndexError, KeyError) as exc:
        raise TaggedMeshError("the mesh artifact does not parse") from exc
    return (
        points,
        np.asarray(corners, dtype=np.int64).reshape(-1, 3),
        np.asarray(tags, dtype=np.int64),
        names,
    )


def gmsh22_triangle_arrays(msh_text: str) -> tuple[np.ndarray, np.ndarray]:
    """Node coordinates and triangle corner rows of an ASCII Gmsh 2.2 artifact.

    The imported solver artifact is always written as ASCII Gmsh 2.2
    (``build_imported_mesh``). A text without a ``$Nodes`` or ``$Elements``
    section holds no surface, and answers empty arrays; anything else that does
    not parse is a ``ValueError``.
    """

    lines = [line.strip() for line in msh_text.splitlines()]
    try:
        version = lines[lines.index("$MeshFormat") + 1].split()
    except (ValueError, IndexError) as exc:
        raise ValueError("the mesh artifact is not an ASCII Gmsh 2.2 mesh") from exc
    if len(version) < 2 or not version[0].startswith("2.") or version[1] != "0":
        raise ValueError(
            "the mesh artifact is not an ASCII Gmsh 2.2 mesh "
            f"(format {' '.join(version)!r})"
        )
    empty = (np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=np.int64))
    if "$Nodes" not in lines or "$Elements" not in lines:
        return empty
    try:
        nodes_start = lines.index("$Nodes")
        node_count = int(lines[nodes_start + 1])
        rows = [line.split() for line in lines[nodes_start + 2 : nodes_start + 2 + node_count]]
        if len(rows) != node_count:
            raise ValueError("the mesh artifact is missing Gmsh nodes")
        index = {row[0]: position for position, row in enumerate(rows)}
        points = np.asarray([row[1:4] for row in rows], dtype=float).reshape(-1, 3)
        elements_start = lines.index("$Elements")
        element_count = int(lines[elements_start + 1])
        corners: list[list[int]] = []
        for line in lines[elements_start + 2 : elements_start + 2 + element_count]:
            parts = line.split()
            if len(parts) < 4 or parts[1] != "2":
                continue
            tag_count = int(parts[2])
            corner = [index[node] for node in parts[3 + tag_count : 6 + tag_count]]
            if len(corner) != 3:
                raise ValueError("the mesh artifact holds a triangle without three nodes")
            corners.append(corner)
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(
            f"the mesh artifact holds malformed Gmsh nodes or elements: {exc}"
        ) from exc
    if not corners:
        return points, empty[1]
    return points, np.asarray(corners, dtype=np.int64)



def _node_section(lines: list[str]) -> tuple[int, int]:
    """Locate the Gmsh 2.2 ``$Nodes`` payload as a ``[start, end)`` line range."""

    try:
        header = lines.index("$Nodes")
        count = int(lines[header + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(
            "a ground-plane solve requires an ASCII Gmsh 2.2 solver artifact"
        ) from exc
    first = header + 2
    return first, first + count


def model_extent(msh_text: str, plane: GroundPlane) -> float:
    """The lowest coordinate the model reaches along the ground plane's axis.

    Read from every node rather than only the used ones: an unused node cannot
    make the placement wrong, and refusing to reason about it keeps this
    agreeing with the package's own check, which is the one that matters.
    """

    lines = msh_text.splitlines()
    first, last = _node_section(lines)
    component = plane.component
    lowest: float | None = None
    for row in lines[first:last]:
        fields = row.split()
        if len(fields) < 4:
            continue
        value = float(fields[1 + component])
        if lowest is None or value < lowest:
            lowest = value
    if lowest is None:
        raise ValueError("solver artifact contains no Gmsh nodes")
    return lowest


def place_above_ground(msh_text: str, plane: GroundPlane) -> str:
    """Translate the solve mesh so it stands ``height_m`` above the plane.

    WG builds every solve mesh in the recentred origin frame -- a full-domain
    OSSE measures x, y in [-65.8, +65.8] mm and z in [-2, +60] mm -- so the
    model straddles all three candidate planes and the package rejects it
    outright.  ``mesh.vertical_offset`` cannot help: ``_solver_mesher_config``
    zeroes it for every solve, deliberately, because a reduced mesh's cut plane
    is pinned to the origin.  The translation therefore belongs here, at the
    adapter boundary, where the mesh is already final and no symmetry cut can
    be disturbed by moving it.

    Only the ground axis column is rewritten; the other two are passed through
    verbatim so a translation cannot perturb coordinates it does not own.  The
    observation frame is derived from this same text downstream, so polar data
    stays referenced to the mouth and follows the model up.
    """

    lowest = model_extent(msh_text, plane)
    if lowest + plane.height_m < 0.0:
        required = -lowest
        raise GroundPlaneClearance(
            f"This model reaches {lowest * 1000.0:.1f} mm along {plane.axis} in "
            f"its own frame, so at a height of {plane.height_m * 1000.0:.1f} mm "
            f"it would cross the ground plane. A rigid half space is bounded at "
            f"{plane.axis} = 0 and the whole model must stay above it: raise the "
            f"height to at least {required * 1000.0:.1f} mm, or turn the ground "
            f"plane off."
        )

    lines = msh_text.splitlines(keepends=True)
    bare = [line.rstrip("\r\n") for line in lines]
    first, last = _node_section(bare)
    component = plane.component
    for index in range(first, last):
        fields = bare[index].split()
        if len(fields) < 4:
            continue
        column = 1 + component
        fields[column] = repr(float(fields[column]) + plane.height_m)
        ending = lines[index][len(bare[index]):]
        lines[index] = " ".join(fields) + ending
    return "".join(lines)
