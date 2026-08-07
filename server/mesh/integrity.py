"""Topology integrity reporting for canonical triangular solve meshes.

The report makes the open-edge guard used by v1 Metal/BEMPP adapters explicit;
see ``server/solver/metal_solver.py:232-252`` and
``server/solver/bempp_solver.py:229-242`` in v1.
"""

from __future__ import annotations

from typing import Any

import numpy as np


SYMMETRY_PLANE_TOLERANCE_M = 1.0e-9


def _first_occurrences(rows: np.ndarray) -> np.ndarray:
    """Mask marking, for each row, whether it is the first of its value.

    The equivalent of walking the array once and asking whether a set has seen
    this row before, which is what the per-triangle loop this replaced did.
    """

    if not len(rows):
        return np.zeros(0, dtype=bool)
    _values, first = np.unique(rows, axis=0, return_index=True)
    mask = np.zeros(len(rows), dtype=bool)
    mask[first] = True
    return mask


def mesh_integrity_report(
    vertices: Any,
    triangles: Any,
    *,
    expected_volume_sign: int | None = 1,
    symmetry_plane_axes: tuple[int, ...] = (),
) -> dict[str, Any]:
    """Report topology and orientation of a canonical triangle mesh.

    Closed canonical meshes default to positive signed volume. Coupled
    infinite-baffle meshes use the opposite interior-domain convention and
    pass ``expected_volume_sign=-1``. Open meshes have no translation-invariant
    volume orientation, but their shared edges must still be consistently
    directed.

    ``symmetry_plane_axes`` names the coordinate axes the mesh was cut on (0
    for the yz plane, 1 for the xz plane). Free edges lying on those planes are
    closed by the solver's mirror and are expected; free edges anywhere else
    are a hole the solver will happily radiate through, which a bare
    ``open_edge_count`` cannot tell apart on a reduced domain.
    """

    if expected_volume_sign not in {-1, 1, None}:
        raise ValueError("expected_volume_sign must be -1, 1, or None")
    if any(axis not in {0, 1, 2} for axis in symmetry_plane_axes):
        raise ValueError("symmetry_plane_axes entries must be 0, 1, or 2")

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
            "off_plane_open_edge_count": 0,
            "off_plane_open_edges_sample": [],
        }

    # Vectorised, because this used to be a Python loop with an np.cross and an
    # np.linalg.norm on a 3-vector *inside* it -- roughly 20-40 us per triangle,
    # so 0.4-0.8 s at the 18,000-triangle warning threshold, and it runs on the
    # single gmsh worker thread that every other mesh operation queues behind.
    # Every count below is defined exactly as the loop defined it, including the
    # order the loop applied its rules in: an out-of-range face is counted and
    # skipped entirely, a repeated-index face is degenerate and skipped, and the
    # remaining faces contribute duplicates, zero-area degeneracy, and edges.
    indices = np.asarray(faces, dtype=np.int64)
    in_range = np.all((indices >= 0) & (indices < len(points)), axis=1)
    invalid = int(np.count_nonzero(~in_range))
    addressable = indices[in_range]

    repeated_index = (
        (addressable[:, 0] == addressable[:, 1])
        | (addressable[:, 1] == addressable[:, 2])
        | (addressable[:, 0] == addressable[:, 2])
    )
    degenerate = int(np.count_nonzero(repeated_index))
    usable = addressable[~repeated_index]

    duplicate = 0
    if len(usable):
        # A face is a duplicate if either key has been seen before it, so it is
        # a duplicate unless it is the first occurrence of *both* keys.
        first_by_index = _first_occurrences(np.sort(usable, axis=1))

        corners = points[usable]
        # Lexicographic by (x, y, z) per triangle, matching the sorted tuple of
        # coordinate tuples the loop built. np.lexsort takes its primary key
        # last.
        order = np.lexsort((corners[:, :, 2], corners[:, :, 1], corners[:, :, 0]), axis=1)
        geometry = np.take_along_axis(corners, order[:, :, None], axis=1).reshape(len(usable), 9)
        first_by_geometry = _first_occurrences(geometry)
        # A tuple containing NaN never equals another, so a face with a
        # non-finite corner was never a geometric duplicate. np.unique collapses
        # NaNs, so restore that.
        first_by_geometry |= ~np.isfinite(geometry).all(axis=1)
        duplicate = int(np.count_nonzero(~(first_by_index & first_by_geometry)))

        cross = np.cross(
            corners[:, 1] - corners[:, 0],
            corners[:, 2] - corners[:, 0],
        )
        finite = np.isfinite(cross).all(axis=1)
        areas = np.zeros(len(usable), dtype=float)
        np.sqrt(np.einsum("ij,ij->i", cross, cross, optimize=True), out=areas, where=finite)
        degenerate += int(np.count_nonzero(~finite | (areas <= 1.0e-15)))

    if len(usable):
        directed = np.concatenate(
            (usable[:, [0, 1]], usable[:, [1, 2]], usable[:, [2, 0]]), axis=0
        )
        directions = np.where(directed[:, 0] < directed[:, 1], 1, -1)
        undirected = np.sort(directed, axis=1)
        unique_edges, inverse, counts = np.unique(
            undirected, axis=0, return_inverse=True, return_counts=True
        )
        inverse = inverse.reshape(-1)
        open_edges = [
            (int(edge[0]), int(edge[1])) for edge in unique_edges[counts == 1]
        ]
        nonmanifold = int(np.count_nonzero(counts > 2))
        # Two faces sharing an edge must traverse it in opposite directions, so
        # their signs sum to zero. A magnitude of two means both agreed, which
        # is the inconsistency.
        sums = np.bincount(inverse, weights=directions, minlength=len(unique_edges))
        inconsistent = int(np.count_nonzero((counts == 2) & (np.abs(sums) == 2)))
    else:
        open_edges = []
        nonmanifold = 0
        inconsistent = 0

    if open_edges and symmetry_plane_axes:
        ends = np.asarray(open_edges, dtype=np.int64)
        on_a_plane = np.zeros(len(ends), dtype=bool)
        for axis in symmetry_plane_axes:
            on_a_plane |= (
                np.abs(points[ends[:, 0], axis]) <= SYMMETRY_PLANE_TOLERANCE_M
            ) & (np.abs(points[ends[:, 1], axis]) <= SYMMETRY_PLANE_TOLERANCE_M)
        off_plane_open_edges = [
            edge for edge, on_plane in zip(open_edges, on_a_plane) if not on_plane
        ]
    else:
        off_plane_open_edges = list(open_edges)
    topologically_closed = (
        len(faces) > 0
        and invalid == 0
        and degenerate == 0
        and nonmanifold == 0
        and duplicate == 0
        and not open_edges
    )
    faces = np.asarray(faces)
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
        "off_plane_open_edge_count": len(off_plane_open_edges),
        "off_plane_open_edges_sample": [
            list(edge) for edge in off_plane_open_edges[:20]
        ],
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
