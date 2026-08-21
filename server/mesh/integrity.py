"""Topology integrity reporting for canonical triangular solve meshes.

The report makes the open-edge guard used by v1 Metal/BEMPP adapters explicit;
see ``server/solver/metal_solver.py:232-252`` and
``server/solver/bempp_solver.py:229-242`` in v1.
"""

from __future__ import annotations

import math
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


# Relative tolerances for the self-intersection test. Both are scaled by a
# length the pair itself owns, never by an absolute millimetre figure: the same
# mesh is checked in metres here and the geometry spans four orders of
# magnitude between a throat facet and a mouth facet.
# Matched to the modelling tolerance OCC itself works to. A tighter figure
# reports sub-micron contact between surfaces that were authored as touching,
# which is noise rather than a defect.
SELF_INTERSECTION_PLANE_EPS = 1.0e-7
SELF_INTERSECTION_SEGMENT_EPS = 1.0e-7
# Candidate pairs are bounded so a pathological mesh degrades to "not checked"
# instead of exhausting memory on the single Gmsh worker. Each surviving pair
# costs ~216 bytes in the adjacency difference alone, so the ceiling is set from
# that budget rather than from an abstract large number.
SELF_INTERSECTION_MAX_CANDIDATE_PAIRS = 4_000_000
_SELF_INTERSECTION_CHUNK = 200_000


def _sweep_and_prune(lower: np.ndarray, upper: np.ndarray) -> np.ndarray | None:
    """Axis-aligned broadphase over triangle boxes.

    Sweep-and-prune rather than a uniform grid: a solver mesh is deliberately
    graded, so mouth facets are an order of magnitude larger than throat
    facets and no single cell size serves both. Sorting on the longest axis and
    keeping an active set costs no tuning constant and degrades gracefully when
    one surface is much coarser than its neighbour.

    Returns ``None`` when the candidate budget is exceeded, which the caller
    reports as "not checked" rather than as "no intersections".
    """

    extent = upper.max(axis=0) - lower.min(axis=0)
    axis = int(np.argmax(extent))
    order = np.argsort(lower[:, axis], kind="stable")
    lo = lower[order]
    hi = upper[order]

    active = np.empty(len(order), dtype=np.int64)
    active_count = 0
    first: list[np.ndarray] = []
    second: list[np.ndarray] = []
    total = 0
    for position in range(len(order)):
        if active_count:
            live = active[:active_count]
            keep = hi[live, axis] >= lo[position, axis]
            live = live[keep]
            active_count = len(live)
            active[:active_count] = live
            if active_count:
                overlap = np.all(
                    (lo[live] <= hi[position]) & (hi[live] >= lo[position]), axis=1
                )
                hits = live[overlap]
                if len(hits):
                    total += len(hits)
                    if total > SELF_INTERSECTION_MAX_CANDIDATE_PAIRS:
                        return None
                    first.append(np.full(len(hits), position, dtype=np.int64))
                    second.append(hits)
        active[active_count] = position
        active_count += 1

    if not first:
        return np.empty((0, 2), dtype=np.int64)
    pairs = np.stack((np.concatenate(first), np.concatenate(second)), axis=1)
    # Back to caller indices; the sweep worked in sorted positions.
    return order[pairs]


def _edge_plane_crossings(
    corners: np.ndarray, distance: np.ndarray, sign: np.ndarray, axis: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Where a triangle's edges cross the other triangle's plane.

    A triangle straddling a plane has exactly one vertex alone on its side; the
    two edges leaving that vertex carry the crossings. Returns both crossing
    points and their coordinate along ``axis``, the dominant component of the
    two planes' intersection line.
    """

    pair_first = sign[:, 0] * sign[:, 1] > 0
    pair_second = sign[:, 0] * sign[:, 2] > 0
    pair_third = sign[:, 1] * sign[:, 2] > 0
    # The first three cases name the vertex opposite an agreeing pair. The rest
    # cover a vertex sitting exactly on the plane, where the remaining two
    # decide which one is alone.
    odd = np.select(
        [pair_first, pair_second, pair_third, sign[:, 0] != 0, sign[:, 1] != 0],
        [2, 1, 0, 0, 1],
        default=2,
    ).astype(np.int64)
    rows = np.arange(len(corners))
    apex_distance = distance[rows, odd]
    apex = corners[rows, odd]

    points = []
    for step in (1, 2):
        other = (odd + step) % 3
        denominator = apex_distance - distance[rows, other]
        # A zero denominator means both ends sit on the plane; the apex is then
        # already the crossing, so a zero parameter is the right answer.
        ratio = np.divide(
            apex_distance,
            denominator,
            out=np.zeros_like(apex_distance),
            where=np.abs(denominator) > 0.0,
        )
        points.append(apex + (corners[rows, other] - apex) * ratio[:, None])

    start, end = points
    return start, end, np.stack(
        (start[rows, axis], end[rows, axis]), axis=1
    )


def _coplanar_overlap(first: np.ndarray, second: np.ndarray, axis: int) -> float:
    """Area of the overlap of two coplanar triangles, projected off ``axis``.

    Sutherland-Hodgman clipping. Only reached for the rare pair whose vertices
    all sit within tolerance of the other's plane, so a Python-level polygon
    clip costs nothing measurable.
    """

    keep = [index for index in range(3) if index != axis]
    subject = [tuple(point[keep]) for point in first]
    clip = [tuple(point[keep]) for point in second]

    def area(polygon: list[tuple[float, float]]) -> float:
        if len(polygon) < 3:
            return 0.0
        total = 0.0
        for index, (x0, y0) in enumerate(polygon):
            x1, y1 = polygon[(index + 1) % len(polygon)]
            total += x0 * y1 - x1 * y0
        return abs(total) / 2.0

    def side(point, start, end) -> float:
        return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (
            point[0] - start[0]
        )

    if area(clip) == 0.0 or area(subject) == 0.0:
        return 0.0
    # Clip against a consistently wound window.
    if sum(
        clip[index][0] * clip[(index + 1) % 3][1]
        - clip[(index + 1) % 3][0] * clip[index][1]
        for index in range(3)
    ) < 0:
        clip = clip[::-1]

    output = subject
    for index in range(len(clip)):
        if not output:
            return 0.0
        start, end = clip[index], clip[(index + 1) % len(clip)]
        current, output = output, []
        for position, point in enumerate(current):
            previous = current[position - 1]
            point_in = side(point, start, end) >= 0.0
            previous_in = side(previous, start, end) >= 0.0
            if point_in != previous_in:
                # The crossing sits where ``side`` reaches zero between the two,
                # so the parameter is previous/(previous - point). Writing the
                # denominator the other way round mirrors the point through
                # ``previous`` and invents vertices outside the subject.
                denominator = side(previous, start, end) - side(point, start, end)
                if denominator != 0.0:
                    ratio = side(previous, start, end) / denominator
                    output.append(
                        (
                            previous[0] + (point[0] - previous[0]) * ratio,
                            previous[1] + (point[1] - previous[1]) * ratio,
                        )
                    )
            if point_in:
                output.append(point)
    return area(output)


def _shared_edge_separates(
    first: np.ndarray, second: np.ndarray, shared: np.ndarray, normal: np.ndarray
) -> bool:
    """True when two coplanar triangles merely tile across the edge they share."""

    edge_points = first[shared]
    if len(edge_points) < 2:
        return False
    start, end = edge_points[0], edge_points[1]
    direction = end - start
    if not np.any(direction):
        return False
    free_first = first[~shared]
    # The partner's free corners are the ones not coincident with the edge.
    offsets = second - start
    along = np.linalg.norm(np.cross(np.broadcast_to(direction, offsets.shape), offsets), axis=1)
    free_second = second[along > 0.0]
    if not len(free_first) or not len(free_second):
        return False

    def side(point: np.ndarray) -> float:
        return float(np.dot(np.cross(direction, point - start), normal))

    return side(free_first[0]) * side(free_second[0]) < 0.0

def mesh_self_intersection_report(
    vertices: Any, triangles: Any, *, max_samples: int = 20
) -> dict[str, Any]:
    """Report triangles of one mesh that pass through each other.

    A mesh can be watertight, manifold, consistently wound and correctly
    oriented while still being geometrically impossible -- a thin-walled
    free-standing waveguide whose coarsely faceted rear shell chords straight
    through the acoustic surface is the case this was written for. The BEM
    boundary is then not the surface the user drew, and the solved field is not
    the field of the intended geometry, but every existing topology check
    passes.

    Pairs sharing a vertex are *not* skipped. A fold-over shares an edge with
    the triangle it folds onto, so excluding adjacency by index would blind the
    test to exactly the defect it exists to find. Adjacent pairs are instead
    tested geometrically and discarded only when the intersection they produce
    is the shared edge itself.
    """

    points = np.asarray(vertices, dtype=float)
    faces = np.asarray(triangles, dtype=np.int64)
    empty = {
        "checked": False,
        "proper_crossing_count": 0,
        "coplanar_overlap_count": 0,
        "intersecting_triangle_count": 0,
        "samples": [],
    }
    if points.ndim != 2 or points.shape[1:] != (3,) or faces.ndim != 2:
        return empty
    if faces.shape[1:] != (3,) or len(faces) == 0:
        return empty
    if not np.all(np.isfinite(points)):
        return empty
    if np.any(faces < 0) or np.any(faces >= len(points)):
        return empty

    corners = points[faces]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normal_length = np.linalg.norm(normals, axis=1)
    # Degenerate faces have no plane to test against and are already counted by
    # mesh_integrity_report; leaving them in would only manufacture noise.
    usable = np.flatnonzero(normal_length > 0.0)
    if len(usable) < 2:
        return {**empty, "checked": True}
    corners = corners[usable]
    faces = faces[usable]
    normals = normals[usable]
    normal_length = normal_length[usable]

    # Faces repeating the same three vertices are duplicates, counted already by
    # mesh_integrity_report. Left in, they pair with each other combinatorially
    # and can exhaust the candidate budget before a real crossing is reached.
    _keys, unique_rows = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    if len(unique_rows) < len(faces):
        unique_rows = np.sort(unique_rows)
        usable = usable[unique_rows]
        corners = corners[unique_rows]
        faces = faces[unique_rows]
        normals = normals[unique_rows]
        normal_length = normal_length[unique_rows]
    if len(faces) < 2:
        return {**empty, "checked": True}

    lower = corners.min(axis=1)
    upper = corners.max(axis=1)
    pairs = _sweep_and_prune(lower, upper)
    if pairs is None:
        return empty
    if not len(pairs):
        return {**empty, "checked": True}

    # Scale every tolerance by a length the PAIR owns, not by the model's
    # bounding diagonal. A single far-away vertex -- another component, or a
    # point no triangle even uses -- would otherwise inflate the weld tolerance
    # until genuinely distinct corners counted as shared and a real crossing was
    # discarded as adjacency. Measured: one unused vertex at 1e9 turned a
    # crossing pair clean.
    extent = np.maximum(upper - lower, 0.0).max(axis=1)

    left, right = pairs[:, 0], pairs[:, 1]

    # Adjacency is decided by POSITION, not by vertex index. An unwelded patch
    # seam -- a collapsed pole row, a CAD import whose faces were never merged --
    # gives neighbouring triangles coincident corners under different indices,
    # and an index-only test then reports their shared edge as a crossing. Going
    # through positions costs one 3x3 compare per pair and is correct for welded
    # and unwelded meshes alike.
    pair_scale = np.maximum(extent[left], extent[right])
    weld = SELF_INTERSECTION_SEGMENT_EPS * pair_scale
    # Chunked: the full (pairs, 3, 3, 3) difference is 216 bytes per pair, so a
    # large candidate set would allocate gigabytes in one go.
    shared = np.empty((len(left), 3), dtype=bool)
    for begin in range(0, len(left), _SELF_INTERSECTION_CHUNK):
        stop = min(begin + _SELF_INTERSECTION_CHUNK, len(left))
        delta = (
            corners[left[begin:stop]][:, :, None, :]
            - corners[right[begin:stop]][:, None, :, :]
        )
        limit = np.square(weld[begin:stop])[:, None, None]
        shared[begin:stop] = (
            np.einsum("ijkl,ijkl->ijk", delta, delta) <= limit
        ).any(axis=2)
    shared_count = shared.sum(axis=1)
    # Three coincident corners is a duplicate face, which mesh_integrity_report
    # already counts; it is not a crossing.

    alive = shared_count < 3
    left, right, shared, shared_count, pair_scale = (
        left[alive],
        right[alive],
        shared[alive],
        shared_count[alive],
        pair_scale[alive],
    )
    if not len(left):
        return {**empty, "checked": True}

    def signed_distance(source: np.ndarray, target: np.ndarray):
        normal = normals[source]
        offset = -np.einsum("ij,ij->i", normal, corners[source][:, 0])
        distance = np.einsum("ij,ikj->ik", normal, corners[target]) + offset[:, None]
        tolerance = SELF_INTERSECTION_PLANE_EPS * normal_length[source] * pair_scale
        on_plane = np.abs(distance) <= tolerance[:, None]
        sign = np.sign(distance)
        sign[on_plane] = 0.0
        # The distance has to agree with the sign it was just given. Leaving a
        # vertex classified as on-plane while its true offset still drives the
        # edge interpolation produces a crossing parameter outside [0, 1] --
        # a fabricated intersection well beyond the triangle that touched.
        distance = np.where(on_plane, 0.0, distance)
        return distance, sign

    distance_right, sign_right = signed_distance(left, right)
    distance_left, sign_left = signed_distance(right, left)

    coplanar = (np.abs(sign_right).sum(axis=1) == 0) & (
        np.abs(sign_left).sum(axis=1) == 0
    )
    # A triangle that keeps to one closed side of the other's plane cannot pass
    # through it; at most the two touch. Requiring every vertex to be strictly
    # off the plane before separating them would miss that, and report an
    # ordinary T-junction -- one triangle's edge lying along a longer edge of
    # another, which is exactly what an imported mesh is full of -- as a
    # crossing.
    def straddles(sign: np.ndarray) -> np.ndarray:
        return ~(np.all(sign >= 0.0, axis=1) | np.all(sign <= 0.0, axis=1))

    keep = coplanar | (straddles(sign_right) & straddles(sign_left))
    if not np.any(keep):
        return {**empty, "checked": True}
    left, right = left[keep], right[keep]
    shared, shared_count = shared[keep], shared_count[keep]
    pair_scale = pair_scale[keep]
    distance_right, sign_right = distance_right[keep], sign_right[keep]
    distance_left, sign_left = distance_left[keep], sign_left[keep]
    coplanar = coplanar[keep]

    crossing_pairs: list[tuple[float, int, int, np.ndarray]] = []
    crossing = np.flatnonzero(~coplanar)
    if len(crossing):
        first, second = left[crossing], right[crossing]
        direction = np.cross(normals[first], normals[second])
        axis = np.argmax(np.abs(direction), axis=1)
        start_left, end_left, span_left = _edge_plane_crossings(
            corners[first], distance_left[crossing], sign_left[crossing], axis
        )
        _s, _e, span_right = _edge_plane_crossings(
            corners[second], distance_right[crossing], sign_right[crossing], axis
        )
        low_left, high_left = span_left.min(axis=1), span_left.max(axis=1)
        low_right, high_right = span_right.min(axis=1), span_right.max(axis=1)
        low = np.maximum(low_left, low_right)
        high = np.minimum(high_left, high_right)

        # The intersection lives on both planes, so where the two triangles
        # share an edge that edge *is* the intersection line and projects onto
        # the same dominant axis exactly. Discarding an overlap that stays
        # inside the shared edge's own span therefore removes ordinary
        # adjacency without hiding a fold that reaches past it.
        along = np.take_along_axis(
            corners[first], np.repeat(axis[:, None, None], 3, axis=1), axis=2
        )[:, :, 0]
        touching = shared[crossing]
        edge_low = np.where(touching, along, np.inf).min(axis=1)
        edge_high = np.where(touching, along, -np.inf).max(axis=1)
        tolerance = SELF_INTERSECTION_SEGMENT_EPS * pair_scale[crossing]
        beyond = (low < edge_low - tolerance) | (high > edge_high + tolerance)
        overlapping = (high > low + tolerance) & beyond

        for row in np.flatnonzero(overlapping):
            width = high_left[row] - low_left[row]
            if width == 0.0:
                continue
            ratio_low = float(np.clip((low[row] - low_left[row]) / width, 0.0, 1.0))
            ratio_high = float(np.clip((high[row] - low_left[row]) / width, 0.0, 1.0))
            # Both ratios are measured from low_left, so they have to be
            # applied from the endpoint that *is* the low end. The crossings
            # come back in edge order, which follows the triangle's winding
            # rather than the axis, so half of all chords run high-to-low.
            # Interpolating those from start_left mirrors the sampled segment
            # about the chord's midpoint and sends the warning to the opposite
            # end of the triangle.
            if span_left[row, 0] <= span_left[row, 1]:
                origin = start_left[row]
                chord = end_left[row] - start_left[row]
            else:
                origin = end_left[row]
                chord = start_left[row] - end_left[row]
            begin = origin + chord * ratio_low
            finish = origin + chord * ratio_high
            length = float(np.linalg.norm(finish - begin))
            crossing_pairs.append(
                (length, int(first[row]), int(second[row]), (begin + finish) / 2.0)
            )

    coplanar_pairs: list[tuple[float, int, int, np.ndarray]] = []
    # A coplanar pair sharing an edge usually just tiles the plane, and a flat
    # rear cap or baffle is thousands of such neighbours -- each one otherwise
    # reaching the Python polygon clip. But sharing an edge does NOT by itself
    # mean they are disjoint: a fold-over hinges on a shared edge and lands on
    # top of its neighbour. They tile only when their free vertices fall on
    # opposite sides of the edge they share, which is what is tested here.
    for row in np.flatnonzero(coplanar):
        if shared_count[row] >= 2 and _shared_edge_separates(
            corners[left[row]], corners[right[row]], shared[row], normals[left[row]]
        ):
            continue
        first, second = int(left[row]), int(right[row])
        axis = int(np.argmax(np.abs(normals[first])))
        overlap = _coplanar_overlap(corners[first], corners[second], axis)
        reference = 0.5 * float(normal_length[first])
        if overlap > reference * SELF_INTERSECTION_SEGMENT_EPS:
            # Reported as the side of the equivalent square, so the sample's
            # "extent" stays a length and can be ranked and printed alongside a
            # crossing's segment length instead of being an area labelled mm.
            coplanar_pairs.append(
                (math.sqrt(overlap), first, second, corners[first].mean(axis=0))
            )

    involved: set[int] = set()
    for _severity, first, second, _where in crossing_pairs + coplanar_pairs:
        involved.add(int(usable[first]))
        involved.add(int(usable[second]))

    ranked = sorted(
        crossing_pairs + coplanar_pairs, key=lambda item: -item[0]
    )[: max(0, int(max_samples))]
    return {
        "checked": True,
        "proper_crossing_count": len(crossing_pairs),
        "coplanar_overlap_count": len(coplanar_pairs),
        "intersecting_triangle_count": len(involved),
        "samples": [
            {
                "triangles": [int(usable[first]), int(usable[second])],
                "location_m": [float(value) for value in where],
                "extent_m": float(severity),
            }
            for severity, first, second, where in ranked
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
