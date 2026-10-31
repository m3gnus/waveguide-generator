"""Strict, bounded Gmsh 2.2 surface import. Display buffers are never solve input."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

import numpy as np

from server.contracts.geometry import SYMMETRY_PLANE_AXIS
from server.mesh.imported import (
    SYMMETRY_SNAP_TOLERANCE_MM,
    _edge_connected_components,
    parse_source_physical_name,
    reflect_triangle_mesh,
    verify_reduced_orientation,
    verify_symmetry_cut,
)
from server.mesh.integrity import mesh_integrity_report, mesh_self_intersection_report, on_coordinate_plane

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


def _name_role(name: str) -> tuple[str, bool]:
    words = re.findall(r"[a-z0-9]+", name.lower())
    for role, prefixes, generic in (
        ("HF", ("hf", "tweeter", "compression", "cd"), False),
        ("MF", ("mf", "mid"), False),
        ("LF", ("lf", "woofer", "bass"), False),
        ("PASSIVE_CARDIOID", ("port", "vent", "passive", "cardioid", "opening"), False),
        ("HF", ("source", "drive", "driver", "diaphragm", "radiat"), True),
    ):
        if any(word.startswith(prefixes) for word in words):
            return role, generic
    return "RIGID", False


def group_role_guess(name: str) -> dict[str, str]:
    """Interpret a group through the descriptor owner, then bounded name hints."""
    descriptor = parse_source_physical_name(name)
    if descriptor is not None:
        role = descriptor["role"]
        label = {"RIGID": "Walls", "PASSIVE_CARDIOID": "Port or passive opening"}.get(
            role, f"{role} source"
        )
        return {"suggested_role": role, "role_from": "file", "display_name": label}
    role, _ = _name_role(name)
    return {"suggested_role": role, "role_from": "default" if role == "RIGID" else "name", "display_name": name}


@dataclass
class FileMesh:
    points: np.ndarray
    triangles: np.ndarray
    tags: np.ndarray
    names: dict[int, str]

    def summary(self) -> dict[str, Any]:
        tags, counts = np.unique(self.tags, return_counts=True)
        groups = []
        generic = []
        for tag, count in zip(tags, counts):
            name = self.names.get(int(tag), "Untagged surface" if tag == 0 else f"Group {tag}")
            guess = group_role_guess(name)
            if guess["role_from"] == "file":
                guess["display_name"] += f" ({tag})"
            group = {"tag": int(tag), "name": name, "triangle_count": int(count), **guess}
            if guess["role_from"] == "name" and _name_role(name)[1]:
                generic.append(group)
            groups.append(group)
        if len(generic) > 1:
            for group in generic:
                group["suggested_role"] = "RIGID"
                group["needs_role_choice"] = True
        extent = float(np.ptp(self.points, axis=0).max())
        return {
            "node_count": len(self.points),
            "triangle_count": len(self.triangles),
            "dimensions": np.ptp(self.points, axis=0).tolist(),
            "groups": groups,
            "suggested_units": "m" if any(group["role_from"] == "file" for group in groups) or extent <= 5 else "mm" if extent >= 20 else None,
        }


def _integer(value: str, maximum: int = 2**31 - 1, minimum: int = 0) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Mesh IDs and counts must be non-negative integers.")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError("Mesh count or ID exceeds the supported limit.")
    return result


def parse_file_mesh(text: str) -> FileMesh:
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
                tag = _integer(match[2])
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
            triangles.append(corners)
            tags.append(element_tags[0] if element_tags else 0)
    if not triangles:
        raise ValueError("Mesh contains no linear triangles.")
    if len(set(tags)) > 256:
        raise ValueError("Mesh import supports at most 256 surface groups.")
    if 0 in tags:
        names[0] = "Untagged surface"
    return FileMesh(coordinates, np.asarray(triangles, dtype=np.int64), np.asarray(tags), names)


def transform_file_mesh(
    mesh: FileMesh, units: str, forward: str, up: str
) -> tuple[FileMesh, dict[str, Any]]:
    """Apply the same SI/axis frame at inspection and preparation."""
    facing, vertical = np.asarray(AXES[forward]), np.asarray(AXES[up])
    if np.dot(facing, vertical):
        raise ValueError("Forward and up must be perpendicular axes.")
    rotation = np.asarray([np.cross(vertical, facing), vertical, facing])
    points = (mesh.points * UNIT_TO_METRES[units]) @ rotation.T
    if not np.isfinite(points).all():
        raise ValueError("Scaled mesh coordinates must be finite.")
    return FileMesh(points, mesh.triangles, mesh.tags, mesh.names), {
        "units": units, "forward": forward, "up": up, "rotation": rotation.tolist(),
    }


def file_mesh_integrity(mesh: FileMesh, planes: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Report file topology with the shared symmetry detector's tolerance."""
    return mesh_integrity_report(
        mesh.points, mesh.triangles,
        symmetry_plane_axes=tuple(SYMMETRY_PLANE_AXIS[plane] for plane in planes),
        symmetry_plane_tolerance=SYMMETRY_SNAP_TOLERANCE_MM * 1e-3,
    )


def _symmetry_evidence(mesh: FileMesh, planes: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    report = file_mesh_integrity(mesh, planes)
    samples = report["off_plane_open_edges_sample"]
    topology = {
        "free_edges": report["open_edge_count"],
        "unexpected_free_edges": report["off_plane_open_edge_count"],
        "unexpected_free_edge_midpoint_samples": [
            (mesh.points[edge].mean(axis=0) * 1000).tolist() for edge in samples
        ],
    }
    return report, verify_symmetry_cut(
        mesh.points * 1000, mesh.triangles, cut_planes=planes, topology=topology,
    )


def inspect_file_mesh(mesh: FileMesh, units: str | None, forward: str, up: str) -> dict[str, Any]:
    summary = mesh.summary()
    transformed, _ = transform_file_mesh(mesh, units or summary["suggested_units"] or "m", forward, up)
    _, verification = _symmetry_evidence(transformed, ())
    sides = verification["detection"]["plane_vertex_side_counts"]
    return {
        **summary,
        "lids": _lid_evidence(transformed, verification),
        "detected_planes": [plane for plane in verification["detected_planes"] if plane in SYMMETRY_PLANE_AXIS],
        "kept_sides": {
            plane: "both" if side["negative"] and side["positive"] else "negative" if side["negative"] else "positive" if side["positive"] else "on_plane"
            for plane, side in sides.items() if plane in SYMMETRY_PLANE_AXIS
        },
    }


def _plane_triangles(mesh: FileMesh, plane: str) -> np.ndarray:
    # The detector in verify_symmetry_cut works in mm with this same tolerance.
    return on_coordinate_plane(
        mesh.points * 1000, mesh.triangles, SYMMETRY_PLANE_AXIS[plane],
        tolerance=SYMMETRY_SNAP_TOLERANCE_MM,
    )


def _lid_evidence(mesh: FileMesh, verification: dict[str, Any]) -> dict[str, Any]:
    lids = {}
    sides = verification["detection"]["plane_vertex_side_counts"]
    for plane in SYMMETRY_PLANE_AXIS:
        mask = _plane_triangles(mesh, plane)
        tags, counts = np.unique(mesh.tags[mask], return_counts=True)
        if not len(tags):
            continue
        side = sides[plane]
        lids[plane] = {
            "triangle_count": int(mask.sum()),
            "groups": [{"tag": int(tag), "triangle_count": int(count)} for tag, count in zip(tags, counts)],
            "candidate": bool(side["negative"]) != bool(side["positive"]),
        }
    return lids


def validate_remove_lid_planes(remove_lid_planes: list[str] | tuple[str, ...], domain_planes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if len(set(remove_lid_planes)) != len(remove_lid_planes) or set(remove_lid_planes) - set(domain_planes):
        raise ValueError("Remove a closed face only on a declared symmetry plane, once per plane.")
    return tuple(plane for plane in domain_planes if plane in remove_lid_planes)


def _check_lid_crossing(planes: tuple[str, ...], straddling: tuple[str, ...] | list[str]) -> None:
    for plane in planes:
        if plane in straddling:
            raise ValueError(f"The mesh crosses {_plane_labels([plane])}. Choose the correct symmetry or change the mesh so it stays on one side.")


def _remove_lids(mesh: FileMesh, planes: tuple[str, ...], verification: dict[str, Any], roles: Mapping[int, str]) -> tuple[FileMesh, dict[str, Any]]:
    """Drop only in-plane triangles; requesting a plane without a lid is an error."""
    _check_lid_crossing(planes, verification.get("straddling_planes", []))
    lids = _lid_evidence(mesh, verification)
    keep = np.ones(len(mesh.triangles), dtype=bool)
    removed = {}
    surface_names = {group["tag"]: group["display_name"] for group in mesh.summary()["groups"]}
    for plane in planes:
        lid = lids.get(plane)
        if lid is None or not lid["candidate"]:
            raise ValueError(f"The mesh has no closed face to remove on {_plane_labels([plane])}. Turn off removal for that plane.")
        for group in lid["groups"]:
            tag = group["tag"]
            if roles.get(tag, "RIGID") != "RIGID":
                raise ValueError(f"Removing the closed face on {_plane_labels([plane])} would remove triangles from driver or port surface {surface_names[tag]!r}. Change the surface groups in the meshing tool.")
        keep &= ~_plane_triangles(mesh, plane)
        removed[plane] = {key: lid[key] for key in ("triangle_count", "groups")}
    if not keep.any():
        raise ValueError("Removing the closed face would leave the mesh with no triangles. Change the mesh in the meshing tool.")
    used, indices = np.unique(mesh.triangles[keep], return_inverse=True)
    kept_tags = set(mesh.tags[keep].tolist())
    names = {tag: name for tag, name in mesh.names.items() if tag in kept_tags}
    return FileMesh(mesh.points[used], indices.reshape(-1, 3), mesh.tags[keep], names), removed


def _opening_message(verification: dict[str, Any]) -> str:
    samples = verification["off_plane_free_edge_samples"]
    near = " (near " + ", ".join(f"{value:.4g}" for value in samples[0]) + " mm)" if samples else ""
    return f"The mesh has an opening that is not on x = 0 or y = 0{near}. Close it in the meshing tool, or declare its symmetry plane in the settings."


def _plane_labels(names: list[str]) -> str:
    return " and ".join("x = 0" if plane == "x0" else "y = 0" for plane in names)


def validate_file_symmetry(
    domain_planes: tuple[str, ...] | list[str],
    reflect_planes: tuple[str, ...] | list[str] | None,
) -> tuple[tuple[str, ...], tuple[str, ...] | None]:
    """Validate and order file symmetry choices for both API and direct callers."""
    if len(set(domain_planes)) != len(domain_planes) or set(domain_planes) - set(SYMMETRY_PLANE_AXIS):
        raise ValueError("Symmetry planes must be x = 0 or y = 0 after axes are applied.")
    planes = tuple(plane for plane in SYMMETRY_PLANE_AXIS if plane in domain_planes)
    if reflect_planes is None:
        return planes, None
    if len(set(reflect_planes)) != len(reflect_planes) or set(reflect_planes) - set(planes):
        raise ValueError("Mirror only across a declared symmetry plane, once per plane.")
    return planes, tuple(plane for plane in planes if plane in reflect_planes)


def prepare_file_mesh(
    mesh: FileMesh, units: str, forward: str, up: str, *,
    domain_planes: tuple[str, ...] | list[str] = (),
    reflect_planes: tuple[str, ...] | list[str] | None = None,
    remove_lid_planes: tuple[str, ...] | list[str] = (),
    roles: Mapping[int, str] | None = None,
    auto_cut: bool = False,
) -> tuple[FileMesh, dict[str, Any]]:
    preparation = prepare_file_mesh_input(
        mesh, units, forward, up, domain_planes=domain_planes, reflect_planes=reflect_planes,
        remove_lid_planes=remove_lid_planes, roles=roles, auto_cut=auto_cut,
    )
    return finish_file_mesh_preparation(preparation)


@dataclass(frozen=True)
class FileMeshPreparation:
    """Validated pure input and original-count admission for the owned cutter."""

    mesh: FileMesh
    transform: dict[str, Any]
    declared_planes: tuple[str, ...]
    candidate_planes: tuple[str, ...]
    roles: dict[int, str]
    input_triangle_count: int

    @property
    def needs_native_cut(self) -> bool:
        if not self.candidate_planes:
            return False
        from server.mesh.file_cut import file_cut_limit_note

        return file_cut_limit_note(self.input_triangle_count) is None


def prepare_file_mesh_input(
    mesh: FileMesh, units: str, forward: str, up: str, *,
    domain_planes: tuple[str, ...] | list[str] = (),
    reflect_planes: tuple[str, ...] | list[str] | None = None,
    remove_lid_planes: tuple[str, ...] | list[str] = (),
    roles: Mapping[int, str] | None = None,
    auto_cut: bool = False,
) -> FileMeshPreparation:
    """Transform, remove lids and verify without importing or starting Gmsh."""
    input_triangle_count = len(mesh.triangles)
    planes, reflect_planes = validate_file_symmetry(domain_planes, reflect_planes)
    remove_lid_planes = validate_remove_lid_planes(remove_lid_planes, planes)
    mesh, transform = transform_file_mesh(mesh, units, forward, up)
    # A requested plane can describe an existing open cut, or ask WG to cut
    # a full mesh. Validate the immutable input before attempting optimisation.
    sides = _symmetry_evidence(mesh, ())[1]["detection"]["plane_vertex_side_counts"]
    crossing = tuple(plane for plane in planes if sides[plane]["positive"] and sides[plane]["negative"])
    _check_lid_crossing(remove_lid_planes, crossing)
    declared = tuple(plane for plane in planes if plane not in crossing)
    if crossing and file_mesh_integrity(mesh, declared)["off_plane_open_edge_count"]:
        raise ValueError(f"The mesh crosses {_plane_labels(list(crossing))} and has an opening away from its declared planes. Close it in the meshing tool.")
    reflections = None if reflect_planes is None else tuple(plane for plane in reflect_planes if plane in declared)
    mesh, transform = _prepare_transformed_file_mesh(
        mesh, transform, declared, reflections, remove_lid_planes, roles or {},
    )
    candidates = crossing or (tuple(SYMMETRY_PLANE_AXIS) if auto_cut and not planes else ())
    return FileMeshPreparation(mesh, transform, declared, candidates, dict(roles or {}), input_triangle_count)


def finish_file_mesh_preparation(
    preparation: FileMeshPreparation, *, cancel_cb: Any = None,
) -> tuple[FileMesh, dict[str, Any]]:
    """Picklable child entry; non-admitted inputs finish without native calls."""
    if cancel_cb is not None:
        cancel_cb()
    mesh, transform = preparation.mesh, preparation.transform
    declared, candidates, roles = preparation.declared_planes, preparation.candidate_planes, preparation.roles
    if not candidates:
        if cancel_cb is not None:
            cancel_cb()
        return mesh, transform
    from server.mesh.file_cut import cut_file_mesh

    reduced, cut = cut_file_mesh(mesh, candidates, roles, input_triangle_count=preparation.input_triangle_count)
    effective = tuple(plane for plane in SYMMETRY_PLANE_AXIS if plane in (*declared, *cut["cut_planes"]))
    if cut["cut_planes"]:
        try:
            reduced, _ = _prepare_transformed_file_mesh(reduced, {}, effective, (), (), roles or {})
        except ValueError:
            # The optimisation never changes a valid full/reduced input.
            reduced = mesh
            cut = {"cut_planes": [], "note": "The cut mesh could not be verified. Export the half or quarter to solve faster."}
            effective = declared
    transform.update(domain_planes=list(effective), cut_planes=cut["cut_planes"], symmetry_report=cut)
    if cancel_cb is not None:
        cancel_cb()
    return reduced, transform


def _prepare_transformed_file_mesh(
    mesh: FileMesh, transform: dict[str, Any], planes: tuple[str, ...],
    reflect_planes: tuple[str, ...] | None, remove_lid_planes: tuple[str, ...],
    roles: Mapping[int, str],
) -> tuple[FileMesh, dict[str, Any]]:
    report, verification = _symmetry_evidence(mesh, planes)
    if remove_lid_planes:
        mesh, removed = _remove_lids(mesh, remove_lid_planes, verification, roles or {})
        transform["removed_lids"] = removed
        report, verification = _symmetry_evidence(mesh, planes)
    sides = verification["detection"]["plane_vertex_side_counts"]
    unreached = [plane for plane in planes if not sides[plane]["on_plane"]]
    if unreached:
        raise ValueError(f"The mesh does not reach {_plane_labels(unreached)}. Choose the correct symmetry plane or change the mesh so it reaches that plane.")
    unnecessary = [plane for plane in (reflect_planes or ()) if sides[plane]["positive"] and not sides[plane]["negative"]]
    if unnecessary:
        raise ValueError(f"The mesh is already on the positive side of {_plane_labels(unnecessary)}; turn mirroring off.")
    reflections = list(reflect_planes) if reflect_planes is not None else [
        plane for plane in planes if sides[plane]["negative"] and not sides[plane]["positive"]
    ]
    if reflections:
        points, triangles, _ = reflect_triangle_mesh(mesh.points, mesh.triangles, reflections, tags=mesh.tags)
        mesh = FileMesh(points, triangles, mesh.tags, mesh.names)
        report, verification = _symmetry_evidence(mesh, planes)
    if not report["valid"] or not report["orientation_valid"]:
        raise ValueError(
            "Solving requires a closed, consistently outward-oriented surface without degenerate, duplicate or nonmanifold triangles. Fix the mesh in its authoring tool; no repair was applied."
        )
    if planes:
        if not verification["verified"]:
            if verification["straddling_planes"]:
                raise ValueError(f"The mesh crosses {_plane_labels(verification['straddling_planes'])}. Choose the correct symmetry or change the mesh so it stays on one side.")
            if verification["wrong_side_planes"]:
                raise ValueError(f"The mesh is on the negative side of {_plane_labels(verification['wrong_side_planes'])}. Enable mirroring in the symmetry settings.")
            if verification["capped_planes"]:
                raise ValueError(f"The mesh has a closed face on {_plane_labels(verification['capped_planes'])}. Remove it in the symmetry settings, or export the mesh open on the symmetry plane.")
            raise ValueError(_opening_message(verification))
        orientation = verify_reduced_orientation(mesh.points * 1000, mesh.triangles, cut_planes=planes)
        if orientation["inverted_component_count"] or orientation["unresolved_component_count"]:
            raise ValueError("Each mirrored mesh shell must face outward. Reverse inward normals or repair the mesh in the meshing tool.")
        transform.update(domain_planes=list(planes), reflected_planes=[plane for plane in planes if plane in reflections])
    elif report["open_edge_count"]:
        detected = [plane for plane in verification["detected_planes"] if plane in SYMMETRY_PLANE_AXIS]
        if detected:
            raise ValueError(f"The mesh is open on {_plane_labels(detected)}. Choose half or quarter symmetry in the settings, or close the mesh in the meshing tool.")
        raise ValueError(_opening_message(verification))
    points = mesh.points
    intersections = mesh_self_intersection_report(points, mesh.triangles)
    if not intersections["checked"] or intersections["intersecting_triangle_count"]:
        raise ValueError(
            "The mesh intersects itself, or exceeds the bounded intersection check. Fix or simplify it in the authoring tool."
        )
    # Check each disconnected shell, not just the sum of its volumes: a small
    # inward shell can hide behind a larger outward one in the aggregate report.
    labels = _edge_connected_components(mesh.triangles)
    count = int(labels.max()) + 1
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
    if not planes and (not np.isfinite(volumes[components]).all() or np.any(volumes[components] <= 0)):
        raise ValueError(
            "Each closed mesh shell must face outward. Reverse inward normals in the authoring tool."
        )
    return mesh, transform


def solve_group_tags(mesh: FileMesh | list[int]) -> dict[int, int]:
    tags = np.unique(mesh.tags if isinstance(mesh, FileMesh) else mesh)
    # Gmsh's solve groups are positive. Keep every existing tag and give the
    # single untagged group the first free positive tag.
    free = next(tag for tag in range(1, len(tags) + 2) if tag not in tags)
    return {int(tag): int(tag) if tag else free for tag in tags}


def mesh_text(mesh: FileMesh) -> str:
    tags = np.unique(mesh.tags)
    written = solve_group_tags(mesh)
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", str(len(tags))]
    lines.extend(f'2 {written[int(tag)]} "{mesh.names.get(int(tag), "Untagged surface" if tag == 0 else f"Group {tag}")}"' for tag in tags)
    lines.extend(["$EndPhysicalNames", "$Nodes", str(len(mesh.points))])
    lines.extend(
        f"{i} " + " ".join(format(float(value), ".17g") for value in point)
        for i, point in enumerate(mesh.points, 1)
    )
    lines.extend(["$EndNodes", "$Elements", str(len(mesh.triangles))])
    lines.extend(
        f"{i} 2 2 {written[int(tag)]} {written[int(tag)]} " + " ".join(str(int(node) + 1) for node in triangle)
        for i, (triangle, tag) in enumerate(zip(mesh.triangles, mesh.tags), 1)
    )
    return "\n".join([*lines, "$EndElements", ""])
