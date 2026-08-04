"""Topology integrity reporting for canonical triangular solve meshes.

The report makes the open-edge guard used by v1 Metal/BEMPP adapters explicit;
see ``server/solver/metal_solver.py:232-252`` and
``server/solver/bempp_solver.py:229-242`` in v1.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np


def mesh_integrity_report(
    vertices: Any,
    triangles: Any,
    *,
    expected_volume_sign: int | None = 1,
) -> dict[str, Any]:
    """Report topology and orientation of a canonical triangle mesh.

    Closed canonical meshes default to positive signed volume. Coupled
    infinite-baffle meshes use the opposite interior-domain convention and
    pass ``expected_volume_sign=-1``. Open meshes have no translation-invariant
    volume orientation, but their shared edges must still be consistently
    directed.
    """

    if expected_volume_sign not in {-1, 1, None}:
        raise ValueError("expected_volume_sign must be -1, 1, or None")

    points = np.asarray(vertices, dtype=float)
    faces = np.asarray(triangles, dtype=np.int64)
    if points.ndim == 1:
        points = points.reshape((-1, 3)) if points.size % 3 == 0 else np.empty((0, 3))
    if faces.ndim == 1:
        faces = faces.reshape((-1, 3)) if faces.size % 3 == 0 else np.empty((0, 3), dtype=np.int64)

    valid_shapes = points.ndim == 2 and points.shape[1:] == (3,) and faces.ndim == 2 and faces.shape[1:] == (3,)
    if not valid_shapes:
        return {
            "valid": False,
            "is_watertight": False,
            "open_edge_count": 0,
            "nonmanifold_edge_count": 0,
            "inconsistent_edge_count": 0,
            "degenerate_triangle_count": 0,
            "invalid_index_triangle_count": 0,
            "duplicate_triangle_count": 0,
            "signed_volume": 0.0,
            "orientation_valid": False,
            "open_edges_sample": [],
        }

    edge_counts: Counter[tuple[int, int]] = Counter()
    edge_directions: dict[tuple[int, int], list[int]] = defaultdict(list)
    degenerate = 0
    invalid = 0
    duplicate = 0
    seen_index_faces: set[tuple[int, int, int]] = set()
    seen_geometric_faces: set[tuple[tuple[float, float, float], ...]] = set()
    for face in faces:
        a, b, c = (int(face[0]), int(face[1]), int(face[2]))
        if min(a, b, c) < 0 or max(a, b, c) >= len(points):
            invalid += 1
            continue
        if len({a, b, c}) < 3:
            degenerate += 1
            continue
        index_key = tuple(sorted((a, b, c)))
        geometric_key = tuple(
            sorted(tuple(float(value) for value in points[index]) for index in (a, b, c))
        )
        if index_key in seen_index_faces or geometric_key in seen_geometric_faces:
            duplicate += 1
        seen_index_faces.add(index_key)
        seen_geometric_faces.add(geometric_key)
        cross = np.cross(points[b] - points[a], points[c] - points[a])
        if not np.all(np.isfinite(cross)) or float(np.linalg.norm(cross)) <= 1.0e-15:
            degenerate += 1
        for first, second in ((a, b), (b, c), (c, a)):
            edge = (min(first, second), max(first, second))
            edge_counts[edge] += 1
            edge_directions[edge].append(1 if first < second else -1)

    open_edges = sorted(edge for edge, count in edge_counts.items() if count == 1)
    nonmanifold = sum(1 for count in edge_counts.values() if count > 2)
    inconsistent = sum(
        1
        for directions in edge_directions.values()
        if len(directions) == 2 and directions[0] == directions[1]
    )
    topologically_closed = (
        len(faces) > 0
        and invalid == 0
        and degenerate == 0
        and nonmanifold == 0
        and duplicate == 0
        and not open_edges
    )
    signed_volume = 0.0
    if len(faces) and invalid == 0:
        p0 = points[faces[:, 0]]
        p1 = points[faces[:, 1]]
        p2 = points[faces[:, 2]]
        signed_volume = float(np.sum(p0 * np.cross(p1, p2)) / 6.0)
    orientation_valid = (
        invalid == 0
        and degenerate == 0
        and nonmanifold == 0
        and duplicate == 0
        and inconsistent == 0
    )
    if topologically_closed and expected_volume_sign is not None:
        orientation_valid = orientation_valid and (
            expected_volume_sign * signed_volume > 0.0
        )
    valid = (
        len(faces) > 0
        and invalid == 0
        and degenerate == 0
        and nonmanifold == 0
        and duplicate == 0
        and orientation_valid
    )
    return {
        "valid": valid,
        "is_watertight": topologically_closed,
        "open_edge_count": len(open_edges),
        "nonmanifold_edge_count": nonmanifold,
        "inconsistent_edge_count": inconsistent,
        "degenerate_triangle_count": degenerate,
        "invalid_index_triangle_count": invalid,
        "duplicate_triangle_count": duplicate,
        "signed_volume": signed_volume,
        "orientation_valid": orientation_valid,
        "open_edges_sample": [list(edge) for edge in open_edges[:20]],
    }


def mesh_semantic_orientation_report(
    vertices: Any,
    triangles: Any,
    surface_tags: Any,
    *,
    mode: str,
) -> dict[str, Any]:
    """Check physical-tag normal semantics at the solver handoff.

    This deliberately reuses the pinned mesher's orientation diagnostics while
    independently checking every driven-source and coupled-aperture face. It
    catches globally reversed open meshes and detached cap reversals that edge
    consistency and signed volume cannot identify.
    """

    points = np.asarray(vertices, dtype=float)
    faces = np.asarray(triangles, dtype=np.int64)
    tags = np.asarray(surface_tags, dtype=np.int32).reshape(-1)
    if (
        points.ndim != 2
        or points.shape[1:] != (3,)
        or faces.ndim != 2
        or faces.shape[1:] != (3,)
        or len(tags) != len(faces)
        or len(faces) == 0
        or np.any(faces < 0)
        or np.any(faces >= len(points))
    ):
        return {
            "valid": False,
            "errors": ["mesh arrays or physical tags are invalid"],
            "source_normal_projection": 0.0,
            "aperture_normal_projection": None,
            "open_shell_bore_alignment": None,
        }

    try:
        from hornlab_mesher.normals import validate_orientation
        from hornlab_mesher.tags import PhysicalGroup

        diagnostic = validate_orientation(
            points,
            faces,
            tags,
            source_axis="z",
            require_watertight=False,
            require_edge_consistency=False,
            require_positive_volume=False,
            require_source_normal=False,
            require_open_shell_bore_normal=False,
        )
    except Exception as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "source_normal_projection": 0.0,
            "aperture_normal_projection": None,
            "open_shell_bore_alignment": None,
        }

    normalized_mode = str(mode or "").strip().lower()
    source_tag = int(PhysicalGroup.PRIMARY_SOURCE)
    aperture_tag = int(PhysicalGroup.MOUTH_APERTURE)
    source_face_projections = _tag_axis_projections(
        points, faces, tags, tag=source_tag
    )
    aperture_face_projections = _tag_axis_projections(
        points, faces, tags, tag=aperture_tag
    )
    scale = max(float(np.ptp(points, axis=0).max()), 1.0e-6)
    area_epsilon = max(1.0e-18, scale * scale * 1.0e-12)
    volume_epsilon = max(1.0e-24, scale * scale * scale * 1.0e-12)
    source_projection = float(np.sum(source_face_projections))
    aperture_projection = (
        float(np.sum(aperture_face_projections))
        if len(aperture_face_projections)
        else None
    )

    errors: list[str] = []
    if int(diagnostic.inconsistent_edges):
        errors.append(
            f"mesh has {int(diagnostic.inconsistent_edges)} inconsistent shared edges"
        )
    if source_projection <= area_epsilon:
        errors.append("primary source must have positive area with +Z normals")
    elif np.any(source_face_projections < -area_epsilon):
        errors.append("primary source contains triangle normals opposite +Z")

    expected_volume_sign = -1 if normalized_mode == "infinite-baffle" else 1
    if diagnostic.watertight and (
        expected_volume_sign * float(diagnostic.signed_volume) <= volume_epsilon
    ):
        direction = "negative" if expected_volume_sign < 0 else "positive"
        errors.append(f"closed {normalized_mode or 'solver'} mesh must have {direction} signed volume")

    if normalized_mode == "bare":
        alignment = diagnostic.open_shell_bore_alignment
        if alignment is None or float(alignment) < 0.9:
            errors.append("bare rigid-wall normals do not face the bore")

    if normalized_mode == "infinite-baffle":
        required_tags = {
            int(PhysicalGroup.RIGID_WALL),
            source_tag,
            aperture_tag,
        }
        missing = sorted(required_tags - {int(value) for value in tags.tolist()})
        if missing:
            errors.append(
                "infinite-baffle mesh is missing required physical tags: "
                + ", ".join(str(value) for value in missing)
            )
        if aperture_projection is None or aperture_projection >= -area_epsilon:
            errors.append("mouth aperture must have negative area with -Z normals")
        elif np.any(aperture_face_projections > area_epsilon):
            errors.append("mouth aperture contains triangle normals opposite -Z")

    return {
        "valid": not errors,
        "errors": errors,
        "source_normal_projection": source_projection,
        "aperture_normal_projection": aperture_projection,
        "open_shell_bore_alignment": diagnostic.open_shell_bore_alignment,
    }


def _tag_axis_projections(
    points: np.ndarray,
    faces: np.ndarray,
    tags: np.ndarray,
    *,
    tag: int,
) -> np.ndarray:
    mask = tags == int(tag)
    if not np.any(mask):
        return np.empty((0,), dtype=float)
    p0 = points[faces[mask, 0]]
    p1 = points[faces[mask, 1]]
    p2 = points[faces[mask, 2]]
    return np.cross(p1 - p0, p2 - p0)[:, 2]


__all__ = ["mesh_integrity_report", "mesh_semantic_orientation_report"]
