"""Strict, bounded Gmsh 2.2 surface import. Display buffers are never solve input."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import numpy as np

from server.mesh.integrity import mesh_integrity_report, mesh_self_intersection_report

MAX_MESH_BYTES = 16 * 1024 * 1024
MAX_NODES = 200_000
MAX_ELEMENTS = 200_000
AXES = {
    "+x": (1, 0, 0),
    "-x": (-1, 0, 0),
    "+y": (0, 1, 0),
    "-y": (0, -1, 0),
    "+z": (0, 0, 1),
    "-z": (0, 0, -1),
}
UNIT_TO_METRES = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254}


@dataclass
class FileMesh:
    points: np.ndarray
    triangles: np.ndarray
    tags: np.ndarray
    names: dict[int, str]

    def summary(self) -> dict[str, Any]:
        tags, counts = np.unique(self.tags, return_counts=True)
        return {
            "node_count": len(self.points),
            "triangle_count": len(self.triangles),
            "dimensions": np.ptp(self.points, axis=0).tolist(),
            "groups": [
                {
                    "tag": int(tag),
                    "name": self.names.get(int(tag), f"Group {tag}"),
                    "triangle_count": int(count),
                }
                for tag, count in zip(tags, counts)
            ],
        }


def _integer(value: str, maximum: int = 2**31 - 1, minimum: int = 0) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Mesh IDs and counts must be non-negative integers.")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError("Mesh count or ID exceeds the supported limit.")
    return result


def parse_file_mesh(text: str, *, require_groups: bool = True) -> FileMesh:
    if len(text.encode("utf-8")) > MAX_MESH_BYTES:
        raise ValueError("Mesh exceeds the 16 MiB import limit.")
    lines = iter(text.strip().splitlines())
    sections: dict[str, list[str]] = {}
    for line in lines:
        marker = line.strip()
        if not marker:
            continue
        if (
            marker not in {"$MeshFormat", "$PhysicalNames", "$Nodes", "$Elements"}
            or marker in sections
        ):
            raise ValueError(f"Unsupported or repeated mesh section: {marker}.")
        end = "$End" + marker[1:]
        rows = []
        for row in lines:
            if row.strip() == end:
                break
            rows.append(row.strip())
        else:
            raise ValueError(f"Missing {end}.")
        sections[marker] = rows
    if sections.get("$MeshFormat") != ["2.2 0 8"]:
        raise ValueError("Import requires an ASCII Gmsh 2.2 mesh (2.2 0 8).")

    def counted(section: str, maximum: int) -> list[str]:
        rows = sections.get(section, [])
        if not rows or _integer(rows[0], maximum) != len(rows) - 1:
            raise ValueError(f"Invalid or missing {section} count.")
        return rows[1:]

    names: dict[int, str] = {}
    if "$PhysicalNames" in sections:
        for line in counted("$PhysicalNames", MAX_ELEMENTS):
            match = re.fullmatch(r'(\d+)\s+(\d+)\s+"([^"\r\n]*)"', line)
            if not match:
                raise ValueError("Invalid physical group name.")
            if match[1] == "2":
                tag = _integer(match[2], minimum=1)
                if tag in names:
                    raise ValueError("Repeated surface physical group ID.")
                names[tag] = match[3]
    points, ids = [], {}
    for line in counted("$Nodes", MAX_NODES):
        parts = line.split()
        if len(parts) != 4:
            raise ValueError("Each mesh node must have exactly three coordinates.")
        node = _integer(parts[0], minimum=1)
        if node in ids:
            raise ValueError("Repeated mesh node ID.")
        ids[node] = len(points)
        points.append([float(value) for value in parts[1:]])
    coordinates = np.asarray(points, dtype=np.float64)
    if not len(points) or not np.isfinite(coordinates).all():
        raise ValueError("Mesh must contain finite node coordinates.")
    triangles, tags, elements = [], [], set()
    for line in counted("$Elements", MAX_ELEMENTS):
        parts = line.split()
        if len(parts) < 3:
            raise ValueError("Invalid mesh element.")
        element, kind, count = (_integer(part) for part in parts[:3])
        if element == 0 or element in elements:
            raise ValueError("Repeated or invalid mesh element ID.")
        elements.add(element)
        expected_nodes = {15: 1, 1: 2, 2: 3}.get(kind)
        if expected_nodes is None:
            raise ValueError(
                "Only linear triangular surfaces are supported; re-export without quadrilaterals, higher-order or volume elements."
            )
        if len(parts) != 3 + count + expected_nodes:
            raise ValueError("Mesh element tag/node count does not match its type.")
        element_tags = [_integer(part) for part in parts[3 : 3 + count]]
        try:
            corners = [ids[_integer(part, minimum=1)] for part in parts[3 + count :]]
        except KeyError as exc:
            raise ValueError("Mesh element references a missing node.") from exc
        if kind == 2:
            if require_groups and (not element_tags or element_tags[0] == 0):
                raise ValueError(
                    "Every triangle needs a positive physical group ID. Assign surface groups before export."
                )
            triangles.append(corners)
            tags.append(element_tags[0] if element_tags else 0)
    if not triangles:
        raise ValueError("Mesh contains no linear triangles.")
    if len(set(tags)) > 256:
        raise ValueError("Mesh import supports at most 256 surface groups.")
    return FileMesh(coordinates, np.asarray(triangles, dtype=np.int64), np.asarray(tags), names)


def prepare_file_mesh(
    mesh: FileMesh, units: str, forward: str, up: str
) -> tuple[FileMesh, dict[str, Any]]:
    facing, vertical = np.asarray(AXES[forward]), np.asarray(AXES[up])
    if np.dot(facing, vertical):
        raise ValueError("Forward and up must be perpendicular axes.")
    rotation = np.asarray([np.cross(vertical, facing), vertical, facing])
    points = (mesh.points * UNIT_TO_METRES[units]) @ rotation.T
    if not np.isfinite(points).all():
        raise ValueError("Scaled mesh coordinates must be finite.")
    report = mesh_integrity_report(points, mesh.triangles)
    if not report["valid"] or not report["is_watertight"] or not report["orientation_valid"]:
        raise ValueError(
            "Solving requires a closed, consistently outward-oriented surface without degenerate, duplicate or nonmanifold triangles. Fix the mesh in its authoring tool; no repair was applied."
        )
    intersections = mesh_self_intersection_report(points, mesh.triangles)
    if not intersections["checked"] or intersections["intersecting_triangle_count"]:
        raise ValueError(
            "The mesh intersects itself, or exceeds the bounded intersection check. Fix or simplify it in the authoring tool."
        )
    # Check each disconnected shell, not just the sum of its volumes: a small
    # inward shell can hide behind a larger outward one in the aggregate report.
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    edges = np.concatenate(
        (mesh.triangles[:, [0, 1]], mesh.triangles[:, [1, 2]], mesh.triangles[:, [2, 0]])
    )
    _edges, inverse = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True)
    owners = np.tile(np.arange(len(mesh.triangles)), 3)[np.argsort(inverse.reshape(-1))].reshape(
        -1, 2
    )
    graph = coo_matrix(
        (np.ones(len(owners)), (owners[:, 0], owners[:, 1])),
        shape=(len(mesh.triangles), len(mesh.triangles)),
    )
    count, labels = connected_components(graph, directed=False)
    # Edge-connected shells may not pinch together at one vertex.
    vertex_shells = np.unique(
        np.column_stack((mesh.triangles.reshape(-1), np.repeat(labels, 3))), axis=0
    )
    if len(np.unique(vertex_shells[:, 0])) != len(vertex_shells):
        raise ValueError(
            "Mesh shells meet at a nonmanifold vertex. Separate or repair the touching shells in the authoring tool."
        )
    face_labels = labels
    components, first = np.unique(face_labels, return_index=True)
    origins = np.zeros((count, 3))
    origins[components] = points[mesh.triangles[first, 0]]
    corners = points[mesh.triangles] - origins[face_labels, None, :]
    volumes = np.bincount(
        face_labels,
        weights=np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])) / 6,
    )
    if not np.isfinite(volumes[components]).all() or np.any(volumes[components] <= 0):
        raise ValueError(
            "Each closed mesh shell must face outward. Reverse inward normals in the authoring tool."
        )
    return FileMesh(points, mesh.triangles, mesh.tags, mesh.names), {
        "units": units,
        "forward": forward,
        "up": up,
        "rotation": rotation.tolist(),
    }


def mesh_text(mesh: FileMesh) -> str:
    tags = np.unique(mesh.tags)
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", str(len(tags))]
    lines.extend(f'2 {tag} "{mesh.names.get(int(tag), f"Group {tag}")}"' for tag in tags)
    lines.extend(["$EndPhysicalNames", "$Nodes", str(len(mesh.points))])
    lines.extend(
        f"{i} " + " ".join(format(float(value), ".17g") for value in point)
        for i, point in enumerate(mesh.points, 1)
    )
    lines.extend(["$EndNodes", "$Elements", str(len(mesh.triangles))])
    lines.extend(
        f"{i} 2 2 {int(tag)} {int(tag)} " + " ".join(str(int(node) + 1) for node in triangle)
        for i, (triangle, tag) in enumerate(zip(mesh.triangles, mesh.tags), 1)
    )
    return "\n".join([*lines, "$EndElements", ""])
