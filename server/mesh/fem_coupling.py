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


# An explicit intake tolerance in export millimetres, independent of OCC's
# Boolean tolerance. Gaps larger than this remain separate air regions.
NEAR_CONTACT_TOLERANCE_MM = 1e-3


def _label(region: Mapping[str, Any], group: Mapping[str, Any] | None = None) -> str:
    result = f"FEM region {region.get('name', region['region_id'])!r}"
    if group is not None:
        result += f", group {group.get('name', group['group_id'])!r}"
    return result


# Facing faces must overlap over more than a pinhole: probes sit on two rings
# this far around the closest points. Edge or corner proximity is not contact.
FACING_PROBE_RADIUS_MM = 10 * NEAR_CONTACT_TOLERANCE_MM
FACING_PROBE_ANGLES = np.radians(np.arange(15, 360, 30))
FACING_MIN_PROBES = 2
# Planes this far from parallel cannot face each other across the tolerance
# over the probe area; their closest points lie on an edge.
FACING_MIN_NORMAL_DOT = .5
# When the rings are inconclusive (an acute wedge whose apex is the closest
# point, or an overlap narrower than the rings), two planar faces are decided
# by the area of their projected overlap inside the tolerance band: exact for
# straight edges, and for curved edges trusted only beyond the chords' error.
# A shared edge or corner has no area; an overlap of more than this does.
FACING_MIN_OVERLAP_MM2 = 1e-8
FACING_CURVE_SEGMENTS = 128
# Otherwise dense samples decide: a cell-centred UV grid over the smaller
# face, and points along both faces' boundary curves stepped this far into
# their own face, so a shared edge or corner never projects onto the other
# face's boundary. Sampled, not exact: a sliver between samples is missed.
FACING_GRID = 32
FACING_BOUNDARY_SAMPLES = 64
FACING_BOUNDARY_INSET_MM = 1e-5
# Planes with normals this close are coplanar only if each face's own
# vertices lie this close to the other's plane.
COPLANAR_NORMAL_DOT = 1 - 1e-12
COPLANAR_DISTANCE_MM = 1e-7


class _Face:
    """Per-face pruning data: inflated box, plane and conservative hull."""

    def __init__(self, gmsh: Any, tag: int, owner: int, position: int, inflate: float):
        self.tag, self.owner, self.position = tag, owner, position
        box = np.asarray(gmsh.model.getBoundingBox(2, tag), dtype=float)
        self.box = box
        self.low, self.high = box[:3] - inflate, box[3:] + inflate
        self.normal = self.offset = None
        corners = np.array([[box[i], box[j], box[k]] for i in (0, 3) for j in (1, 4) for k in (2, 5)])
        self.hull = corners
        if gmsh.model.getType(2, tag) != "Plane":
            return
        lower, upper = (np.asarray(v, dtype=float) for v in gmsh.model.getParametrizationBounds(2, tag))
        middle = ((lower + upper) / 2).tolist()
        normal = np.asarray(gmsh.model.getNormal(tag, middle), dtype=float)
        if not np.isfinite(normal).all() or np.linalg.norm(normal) == 0:
            return
        self.normal = normal / np.linalg.norm(normal)
        self.offset = float(self.normal @ np.asarray(gmsh.model.getValue(2, tag, middle), dtype=float))
        # A planar face lies in the convex hull of its boundary. Straight
        # edges contribute their end points; any other curve its box corners.
        points = []
        for _dim, curve in gmsh.model.getBoundary([(2, tag)], combined=False, oriented=False):
            curve = abs(int(curve))
            if gmsh.model.getType(1, curve) == "Line":
                points.extend(gmsh.model.getValue(0, abs(int(vertex)), [])
                              for _d, vertex in gmsh.model.getBoundary([(1, curve)], oriented=False))
            else:
                b = gmsh.model.getBoundingBox(1, curve)
                points.extend([b[i], b[j], b[k]] for i in (0, 3) for j in (1, 4) for k in (2, 5))
        if points:
            self.hull = np.asarray(points, dtype=float).reshape(-1, 3)


def _beyond_plane(plane: _Face, other: _Face, gap: float) -> bool:
    """True when ``other`` lies wholly more than ``gap`` from ``plane``'s plane."""
    if plane.normal is None:
        return False
    signed = other.hull @ plane.normal - plane.offset
    return bool(signed.min() > gap or signed.max() < -gap)


def _on_plane(plane: _Face, other: _Face) -> bool:
    """True when every hull point of ``other`` lies on ``plane``'s plane."""
    signed = other.hull @ plane.normal - plane.offset
    return bool(np.abs(signed).max() <= COPLANAR_DISTANCE_MM)


def _cannot_face(a: _Face, b: _Face, gap: float) -> bool:
    """Exact pre-filters before an OCC distance; each keeps every facing pair."""
    if a.normal is not None and b.normal is not None:
        dot = abs(float(a.normal @ b.normal))
        if dot < FACING_MIN_NORMAL_DOT:
            return True
        if dot > COPLANAR_NORMAL_DOT and _on_plane(a, b) and _on_plane(b, a):
            # Coplanar: any separation is tangential to both faces. Measured
            # at the faces' own vertices, not by offsets from the origin:
            # nearly parallel planes diverge with distance from their crossing.
            return True
    return _beyond_plane(a, b, gap) or _beyond_plane(b, a, gap)


def _candidate_pairs(faces: Sequence[_Face]) -> list[tuple[_Face, _Face]]:
    """Sort-and-sweep on inflated boxes; only pairs from different regions."""
    if not faces:
        return []
    low = np.array([f.low for f in faces])
    high = np.array([f.high for f in faces])
    owner = np.array([f.owner for f in faces])
    order = np.argsort(low[:, 0], kind="stable")
    sorted_low = low[order, 0]
    pairs = []
    for rank, index in enumerate(order):
        end = int(np.searchsorted(sorted_low, high[index, 0], side="right"))
        others = order[rank + 1:end]
        others = others[(owner[others] != owner[index])
                        & np.all(low[others, 1:] <= high[index, 1:], axis=1)
                        & np.all(high[others, 1:] >= low[index, 1:], axis=1)]
        for other in others:
            first, second = (faces[index], faces[other])
            if (first.owner, first.position) > (second.owner, second.position):
                first, second = second, first
            pairs.append((first, second))
    pairs.sort(key=lambda pair: (pair[0].owner, pair[0].position, pair[1].owner, pair[1].position))
    return pairs


def _facing_probes(gmsh: Any, a: int, b: int, p: np.ndarray, normal: np.ndarray, tolerance: float) -> int:
    """Count probes around ``p`` on face ``a`` that face face ``b`` within the gap."""
    helper = np.eye(3)[np.argmin(np.abs(normal))]
    u = np.cross(normal, helper)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    offsets = [radius * (np.cos(angle) * u + np.sin(angle) * v)
               for radius in (FACING_PROBE_RADIUS_MM / 2, FACING_PROBE_RADIUS_MM)
               for angle in FACING_PROBE_ANGLES]
    on_a, parameters_a = gmsh.model.getClosestPoint(2, a, (p + np.asarray(offsets)).ravel().tolist())
    on_a = np.asarray(on_a, dtype=float).reshape(-1, 3)
    parameters_a = np.asarray(parameters_a, dtype=float).reshape(-1, 2)
    kept = [i for i, uv in enumerate(parameters_a) if gmsh.model.isInside(2, a, uv.tolist(), True)]
    if len(kept) < FACING_MIN_PROBES:
        return 0
    on_b, parameters_b = gmsh.model.getClosestPoint(2, b, on_a[kept].ravel().tolist())
    on_b = np.asarray(on_b, dtype=float).reshape(-1, 3)
    parameters_b = np.asarray(parameters_b, dtype=float).reshape(-1, 2)
    count = 0
    for start, end, uv_a, uv_b in zip(on_a[kept], on_b, parameters_a[kept], parameters_b):
        gap = end - start
        length = float(np.linalg.norm(gap))
        if length > tolerance + 1e-7 or not gmsh.model.isInside(2, b, uv_b.tolist(), True):
            continue
        n_a = np.asarray(gmsh.model.getNormal(a, uv_a.tolist()), dtype=float)
        n_b = np.asarray(gmsh.model.getNormal(b, uv_b.tolist()), dtype=float)
        if abs(float(n_a @ n_b)) < FACING_MIN_NORMAL_DOT:
            continue
        if length > 1e-9 and abs(float(gap @ n_a)) < FACING_MIN_NORMAL_DOT * length:
            continue
        count += 1
    return count


def _normals(gmsh: Any, tag: int, parameters: np.ndarray) -> np.ndarray:
    normals = np.asarray(gmsh.model.getNormal(tag, parameters.ravel().tolist()), dtype=float).reshape(-1, 3)
    return normals / np.maximum(np.linalg.norm(normals, axis=1), 1e-300)[:, None]


def _inside(gmsh: Any, tag: int, parameters: np.ndarray) -> np.ndarray:
    return np.array([bool(gmsh.model.isInside(2, tag, uv.tolist(), True)) for uv in parameters], dtype=bool)


def _facing_samples(gmsh: Any, a: int, b: int, points: np.ndarray, tolerance: float) -> bool:
    """True when any point on face ``a`` faces face ``b`` within the gap."""
    if not len(points):
        return False
    on_a, uv_a = gmsh.model.getClosestPoint(2, a, points.ravel().tolist())
    on_a = np.asarray(on_a, dtype=float).reshape(-1, 3)
    uv_a = np.asarray(uv_a, dtype=float).reshape(-1, 2)
    keep = _inside(gmsh, a, uv_a)
    if not keep.any():
        return False
    on_a, uv_a = on_a[keep], uv_a[keep]
    on_b, uv_b = gmsh.model.getClosestPoint(2, b, on_a.ravel().tolist())
    on_b = np.asarray(on_b, dtype=float).reshape(-1, 3)
    uv_b = np.asarray(uv_b, dtype=float).reshape(-1, 2)
    gap = on_b - on_a
    length = np.linalg.norm(gap, axis=1)
    close = length <= tolerance + 1e-7
    if not close.any():
        return False
    n_a = _normals(gmsh, a, uv_a[close])
    n_b = _normals(gmsh, b, uv_b[close])
    gap, length = gap[close], length[close]
    facing = ((np.abs(np.einsum("ij,ij->i", n_a, n_b)) >= FACING_MIN_NORMAL_DOT)
              & ((length <= 1e-9)
                 | (np.abs(np.einsum("ij,ij->i", gap, n_a)) >= FACING_MIN_NORMAL_DOT * length)))
    if not facing.any():
        return False
    return bool(_inside(gmsh, b, uv_b[close][facing]).any())


def _grid_samples(gmsh: Any, tag: int) -> np.ndarray:
    """Cell-centred points over a face's parameter domain (trimmed later)."""
    lower, upper = (np.asarray(v, dtype=float) for v in gmsh.model.getParametrizationBounds(2, tag))
    fractions = (np.arange(FACING_GRID) + .5) / FACING_GRID
    u, v = np.meshgrid(lower[0] + fractions * (upper[0] - lower[0]),
                       lower[1] + fractions * (upper[1] - lower[1]), indexing="ij")
    parameters = np.column_stack([u.ravel(), v.ravel()])
    return np.asarray(gmsh.model.getValue(2, tag, parameters.ravel().tolist()), dtype=float).reshape(-1, 3)


def _boundary_samples(gmsh: Any, tag: int) -> np.ndarray:
    """Points along a face's boundary curves, stepped into the face on both sides.

    Curve end points are not used: at a corner a step along one edge lands on
    the other edge, and a shared edge must not count as an overlap.
    """
    samples = []
    for _dim, curve in gmsh.model.getBoundary([(2, tag)], combined=False, oriented=False):
        curve = abs(int(curve))
        lower, upper = (float(np.ravel(v)[0]) for v in gmsh.model.getParametrizationBounds(1, curve))
        parameters = lower + (np.arange(FACING_BOUNDARY_SAMPLES) + .5) / FACING_BOUNDARY_SAMPLES * (upper - lower)
        points = np.asarray(gmsh.model.getValue(1, curve, parameters.tolist()), dtype=float).reshape(-1, 3)
        tangents = np.asarray(gmsh.model.getDerivative(1, curve, parameters.tolist()), dtype=float).reshape(-1, 3)
        uv = np.asarray(gmsh.model.getParametrization(2, tag, points.ravel().tolist()), dtype=float).reshape(-1, 2)
        across = np.cross(_normals(gmsh, tag, uv), tangents)
        lengths = np.linalg.norm(across, axis=1)
        valid = lengths > 0
        across = across[valid] / lengths[valid, None]
        points = points[valid]
        samples.extend([points + FACING_BOUNDARY_INSET_MM * across, points - FACING_BOUNDARY_INSET_MM * across])
    return np.concatenate(samples) if samples else np.empty((0, 3))


def _face_width(gmsh: Any, tag: int) -> float:
    """A face's characteristic width, twice its area over its perimeter."""
    area = float(gmsh.model.occ.getMass(2, tag))
    perimeter = sum(float(gmsh.model.occ.getMass(1, abs(int(curve))))
                    for _dim, curve in gmsh.model.getBoundary([(2, tag)], combined=False, oriented=False))
    return 2 * area / perimeter if perimeter > 0 else 0.


def _planar_segments(gmsh: Any, tag: int, origin: np.ndarray, u: np.ndarray,
                      v: np.ndarray) -> tuple[np.ndarray, float]:
    """A planar face's boundary as counter-clockwise (u, v) segments.

    Straight edges are exact; other curves become chords, and the second
    value bounds the area between the chords and the curves.
    """
    segments, error = [], 0.
    for _dim, curve in gmsh.model.getBoundary([(2, tag)], combined=False, oriented=True):
        reverse, curve = curve < 0, abs(int(curve))
        lower, upper = (float(np.ravel(bound)[0]) for bound in gmsh.model.getParametrizationBounds(1, curve))
        straight = gmsh.model.getType(1, curve) == "Line"
        parameters = np.linspace(lower, upper, 2 if straight else FACING_CURVE_SEGMENTS + 1)
        points = np.asarray(gmsh.model.getValue(1, curve, parameters.tolist()), dtype=float).reshape(-1, 3)
        if not straight:
            middles = np.asarray(gmsh.model.getValue(1, curve, ((parameters[:-1] + parameters[1:]) / 2).tolist()),
                                 dtype=float).reshape(-1, 3)
            chords = np.linalg.norm(points[1:] - points[:-1], axis=1)
            sagitta = np.linalg.norm(middles - (points[1:] + points[:-1]) / 2, axis=1)
            # A chord's sagitta bounds the curve's offset from it for arcs and
            # short smooth pieces; doubled for margin.
            error += 2 * float(np.sum(chords * sagitta))
        xy = np.column_stack([(points - origin) @ u, (points - origin) @ v])
        if reverse:
            xy = xy[::-1]
        segments.append(np.stack([xy[:-1], xy[1:]], axis=1))
    segments = np.concatenate(segments) if segments else np.empty((0, 2, 2))
    signed = .5 * float(np.sum(segments[:, 0, 0] * segments[:, 1, 1] - segments[:, 1, 0] * segments[:, 0, 1]))
    return (segments[:, ::-1] if signed < 0 else segments), error


def _convex_clip(polygon: np.ndarray, normal: np.ndarray, limit: float) -> np.ndarray:
    """Sutherland-Hodgman clip of a convex polygon to ``normal . x <= limit``."""
    result = []
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0)):
        a, b = float(start @ normal) - limit, float(end @ normal) - limit
        if a <= 0:
            result.append(start)
        if (a < 0 < b) or (b < 0 < a):
            result.append(start + a / (a - b) * (end - start))
    return np.asarray(result).reshape(-1, 2)


def _overlap_area(polygons: Sequence[np.ndarray], floor: float) -> float:
    """Exact area common to counter-clockwise polygons given as segments.

    A polygon's indicator above ``floor`` is the signed sum, over its
    non-vertical segments, of "below this segment": +1 for leftward (upper)
    segments, -1 for rightward. The product of the indicators integrates
    term by term; each term is a piecewise-linear integral in x.
    """
    weighted = []
    for segments in polygons:
        x0, y0, x1, y1 = segments[:, 0, 0], segments[:, 0, 1], segments[:, 1, 0], segments[:, 1, 1]
        keep = (x0 != x1) & (np.maximum(y0, y1) > floor)
        x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
        sign = np.where(x1 < x0, 1., -1.)
        low, high = np.minimum(x0, x1), np.maximum(x0, x1)
        slope = (y1 - y0) / (x1 - x0)
        weighted.append((low, high, slope, y0 - slope * x0, sign))
    if any(not len(terms[0]) for terms in weighted):
        return 0.
    # Every combination whose x ranges overlap, built one polygon at a time.
    index = [np.arange(len(weighted[0][0]))[:, None]]
    left, right = weighted[0][0], weighted[0][1]
    for terms in weighted[1:]:
        overlap = (left[:, None] < terms[1][None, :]) & (terms[0][None, :] < right[:, None])
        rows, columns = np.nonzero(overlap)
        index = [np.column_stack([index[0][rows], columns])]
        left, right = np.maximum(left[rows], terms[0][columns]), np.minimum(right[rows], terms[1][columns])
    combination = index[0]
    if not len(combination):
        return 0.
    slopes = np.column_stack([terms[2][combination[:, k]] for k, terms in enumerate(weighted)])
    intercepts = np.column_stack([terms[3][combination[:, k]] for k, terms in enumerate(weighted)])
    signs = np.prod(np.column_stack([terms[4][combination[:, k]] for k, terms in enumerate(weighted)]), axis=1)
    # Breakpoints: the ends, every pairwise crossing and every floor crossing.
    breaks = [left[:, None], right[:, None]]
    count = slopes.shape[1]
    with np.errstate(divide="ignore", invalid="ignore"):
        for i in range(count):
            breaks.append(((floor - intercepts[:, i]) / slopes[:, i])[:, None])
            for j in range(i + 1, count):
                breaks.append(((intercepts[:, j] - intercepts[:, i]) / (slopes[:, i] - slopes[:, j]))[:, None])
    x = np.concatenate(breaks, axis=1)
    x = np.where(np.isfinite(x), x, left[:, None])
    x = np.sort(np.clip(x, left[:, None], right[:, None]), axis=1)
    height = np.maximum((slopes[:, None, :] * x[:, :, None] + intercepts[:, None, :]).min(axis=2) - floor, 0)
    area = np.sum(.5 * (height[:, 1:] + height[:, :-1]) * np.diff(x, axis=1), axis=1)
    return float(np.sum(signs * area))


def _planar_overlap(gmsh: Any, a: int, b: int, p: np.ndarray, n_a: np.ndarray,
                    n_b: np.ndarray, tolerance: float) -> bool | None:
    """Decide two planar faces by their projected overlap inside the gap.

    Face ``b`` is projected along ``n_a`` onto ``a``'s plane, and the overlap
    is cut to the band where the gap along ``n_a`` is within the tolerance.
    True or False when decided; None when curved edges leave it uncertain.
    """
    helper = np.eye(3)[np.argmin(np.abs(n_a))]
    u = np.cross(n_a, helper)
    u /= np.linalg.norm(u)
    v = np.cross(n_a, u)
    first, error_a = _planar_segments(gmsh, a, p, u, v)
    second, error_b = _planar_segments(gmsh, b, p, u, v)
    if not len(first) or not len(second):
        return None
    low = np.maximum(first.reshape(-1, 2).min(axis=0), second.reshape(-1, 2).min(axis=0))
    high = np.minimum(first.reshape(-1, 2).max(axis=0), second.reshape(-1, 2).max(axis=0))
    if np.any(low >= high):
        return False if error_a == error_b == 0 else None
    band = np.array([low, [high[0], low[1]], high, [low[0], high[1]]])
    # Gap along n_a from a's plane (through p) to b's plane, linear in (u, v).
    b_point = np.asarray(gmsh.model.getValue(2, b, gmsh.model.getParametrization(2, b, p.tolist())), dtype=float)
    along = float(n_b @ n_a)
    gap0 = float(n_b @ (b_point - p)) / along
    gradient = -np.array([n_b @ u, n_b @ v]) / along
    limit = tolerance + 1e-7
    band = _convex_clip(band, gradient, limit - gap0)
    if len(band) >= 3:
        band = _convex_clip(band, -gradient, limit + gap0)
    if len(band) < 3:
        return False if error_a == error_b == 0 else None
    third = np.stack([band, np.roll(band, -1, axis=0)], axis=1)
    area = _overlap_area([first, second, third], float(band[:, 1].min()))
    # Rounding scales with the band's extent; chords with the curves' error.
    rounding = 1e-12 * float(np.ptp(band, axis=0) @ np.ptp(band, axis=0))
    error = error_a + error_b
    if area > FACING_MIN_OVERLAP_MM2 + rounding + error:
        return True
    return False if error == 0 else None


def _faces_face_each_other(gmsh: Any, a: int, b: int, p: np.ndarray, q: np.ndarray,
                           normals: Sequence[np.ndarray], tolerance: float) -> bool:
    """Require an overlapping projected area, not just near edges or corners.

    Conservative: a false refusal is cheaper than accepting a contact. Rings
    around the closest points decide the common case. Two planar faces are
    then decided by their overlap area (exact for straight edges). Any other
    pair refuses when either face is narrower than the rings or any dense
    sample lies over an overlap; only then is edge or corner proximity
    accepted, and a sliver between the samples can be missed.
    """
    count = _facing_probes(gmsh, a, b, p, normals[0], tolerance)
    if count >= FACING_MIN_PROBES:
        return True
    if count + _facing_probes(gmsh, b, a, q, normals[1], tolerance) >= FACING_MIN_PROBES:
        return True
    n_a, n_b = (np.asarray(normal, dtype=float) / max(np.linalg.norm(normal), 1e-300) for normal in normals)
    if abs(float(n_a @ n_b)) >= FACING_MIN_NORMAL_DOT and all(
            gmsh.model.getType(2, tag) == "Plane" for tag in (a, b)):
        decided = _planar_overlap(gmsh, a, b, p, n_a, n_b, tolerance)
        if decided is not None:
            return decided
    if (abs(float(n_a @ n_b)) >= FACING_MIN_NORMAL_DOT
            and min(_face_width(gmsh, a), _face_width(gmsh, b)) < FACING_PROBE_RADIUS_MM):
        return True
    small, large = (a, b) if gmsh.model.occ.getMass(2, a) <= gmsh.model.occ.getMass(2, b) else (b, a)
    return (_facing_samples(gmsh, small, large, _grid_samples(gmsh, small), tolerance)
            or _facing_samples(gmsh, a, b, _boundary_samples(gmsh, a), tolerance)
            or _facing_samples(gmsh, b, a, _boundary_samples(gmsh, b), tolerance))


def _near_contact_problem(gmsh: Any, region_data: Sequence[Any], *,
                          exact_moving_contacts: bool = False) -> str | None:
    """Check separated face contacts before Boolean operations remove parents.

    Only faces that face each other count: opposite-facing faces, separated
    along both normals by at most the tolerance, over an overlapping area
    larger than a pinhole. Two regions that only share an edge or a corner,
    a hair apart, are not in contact. Inflated boxes in a sort-and-sweep, then
    plane pre-filters, prune pairs before OCC's exact trimmed-face distance.
    Exact contacts are checked using shared topology after fragmentation.

    ``exact_moving_contacts`` (coupled meshes with exterior ports) also
    refuses a same-channel moving pair inside the tolerance: OCC does not
    merge it into one shared face, and only an exact contact can be coupled.
    """
    tolerance = NEAR_CONTACT_TOLERANCE_MM
    groups = []
    faces = []
    for owner, (region, _volumes, boundary, assigned) in enumerate(region_data):
        groups.append({tag: group for group in region["surface_groups"]
                       for tag in assigned[group["group_id"]]})
        faces.extend(_Face(gmsh, tag, owner, position, (tolerance + 1e-7) / 2)
                     for position, tag in enumerate(boundary))

    def contact_label(region, group):
        return _label(region, group) if group else f"{_label(region)}, group '_walls' (implicit rigid)"

    for face_a, face_b in _candidate_pairs(faces):
        a, b = face_a.tag, face_b.tag
        low = np.maximum(face_a.box[:3], face_b.box[:3])
        high = np.minimum(face_a.box[3:], face_b.box[3:])
        if np.linalg.norm(np.maximum(low - high, 0)) > tolerance + 1e-7:
            continue
        if _cannot_face(face_a, face_b, tolerance + 1e-7):
            continue
        first, second = region_data[face_a.owner][0], region_data[face_b.owner][0]
        distance, *coordinates = gmsh.model.occ.getDistance(2, a, 2, b)
        if distance < 0:
            return f"{_label(first)} and {_label(second)}: could not check near-contact face distance."
        if not 1e-7 < distance <= tolerance + 1e-7:
            continue
        p, q = np.asarray(coordinates).reshape(2, 3)
        direction = (q - p) / distance
        normals = [np.asarray(gmsh.model.getNormal(tag, gmsh.model.getParametrization(2, tag, point)))
                   for tag, point in ((a, p), (b, q))]
        if any(abs(np.dot(direction, normal)) < 1e-6 for normal in normals):
            continue
        if not _faces_face_each_other(gmsh, a, b, p, q, normals, tolerance):
            continue
        left, right = groups[face_a.owner].get(a), groups[face_b.owner].get(b)
        if (left and right and left['kind'] == right['kind'] == 'moving'
                and left.get('drive_channel_id')
                and left.get('drive_channel_id') == right.get('drive_channel_id')):
            if not exact_moving_contacts:
                continue
            return (
                f"{contact_label(first, left)} and {contact_label(second, right)} are near-touching "
                f"moving faces of drive channel {left['drive_channel_id']!r} ({distance:.6g} mm apart; "
                f"tolerance {tolerance:g} mm). With exterior ports, near-touching moving faces must be "
                f"either exactly coincident or separated by more than {tolerance:g} mm."
            )
        return (
            f"{contact_label(first, left)} and {contact_label(second, right)} are in near-contact "
            f"({distance:.6g} mm; tolerance {tolerance:g} mm); shared FEM air boundaries "
            "require moving faces on both sides of the same electrodynamic channel."
        )
    return None


def _solid_size(gmsh: Any, volumes: Sequence[int]) -> tuple[float, float]:
    """Volume (mm^3) and boundary area (mm^2) of OCC solids."""
    volume = sum(gmsh.model.occ.getMass(3, int(tag)) for tag in volumes)
    area = sum(gmsh.model.occ.getMass(2, tag) for tag in _boundary(gmsh, volumes))
    return float(volume), float(area)


def _air_volumes(gmsh: Any, region: Mapping[str, Any], volumes: Sequence[int],
                 material: set[int], size: tuple[float, float], *, contacts_only: bool) -> list[int]:
    """Region air after fragmentation, refusing overlaps by their measured size.

    A piece shared with assembly material whose thickness (2 V / S) is within
    the near-contact tolerance is a CAD face a few nanometres off, not an
    overlap. Interior-only regions mesh their own solid in an independent
    child, so such slivers are left with the material. A coupled mesh would
    have to mesh the sliver itself, so it still needs an exact fit.
    """
    tolerance = NEAR_CONTACT_TOLERANCE_MM
    volume, area = size
    shared = [tag for tag in volumes if tag in material]
    air = [tag for tag in volumes if tag not in material]
    if not volumes:
        raise FemRegionMeshError(
            f"{_label(region)} lost its volume: none of its {volume:.6g} mm³ survived fragmentation."
        )
    if not air:
        raise FemRegionMeshError(
            f"{_label(region)} overlaps assembly material by {volume:.6g} mm³ (100% of its volume); "
            "no air remains."
        )
    if not shared:
        return air
    pieces = [_solid_size(gmsh, [tag]) for tag in shared]
    overlap = sum(piece for piece, _area in pieces)
    percent = 100 * overlap / volume if volume > 0 else 100.
    thickness = max(2 * piece / max(surface, 1e-300) for piece, surface in pieces)
    measured = (f"by {overlap:.3g} mm³ ({percent:.3g}% of its {volume:.6g} mm³ volume; "
                f"overlap thickness up to {thickness:.3g} mm)")
    sliver = thickness <= tolerance and overlap <= tolerance * area
    if sliver and contacts_only:
        return air
    if sliver:
        raise FemRegionMeshError(
            f"{_label(region)} overlaps assembly material {measured}, a sliver within the "
            f"{tolerance:g} mm tolerance; a region with exterior ports must meet the material "
            "exactly: make its faces coincide with the cavity walls."
        )
    raise FemRegionMeshError(f"{_label(region)} overlaps assembly material {measured}.")


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
    Interior-only intake uses contacts_only for every region count: it pays
    for one assembly/region replay and Boolean fragmentation, including the
    material-overlap gate, but generates no surface or tetrahedral mesh.
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
        region_sizes: dict[str, tuple[float, float]] = {}
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
            region_sizes[region["region_id"]] = _solid_size(gmsh, volumes)
        near_contact_problem = _near_contact_problem(
            gmsh, region_data, exact_moving_contacts=not contacts_only)
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
            volumes = _air_volumes(gmsh, region, volumes, material_volumes,
                                   region_sizes[region["region_id"]], contacts_only=contacts_only)
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
        if near_contact_problem:
            raise FemRegionMeshError(near_contact_problem)
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
                interface_labels = ', '.join(
                    f"group {group.get('name', group['group_id'])!r}"
                    for group in region['surface_groups'] if group['kind'] == 'interface')
                raise FemRegionMeshError(
                    f"{_label(region)}, {interface_labels} has uncovered air boundary faces; "
                    "the exterior is not closed by declared interfaces."
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
                               boundary, volumes, group['kind'] == 'moving'))
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
            *(size for _tags, size, _base, _boundary_tags, _volumes, _moving in sizing),
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
        for tags, size, base, boundary, volumes, moving in sizing:
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
            if not touching_regions or not moving:
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
        message = str(exc)
        if "FEM region" not in message:
            message = f"{labels}: {message}"
        raise FemRegionMeshError(message, symmetry_plane=exc.symmetry_plane) from exc
    except Exception as exc:
        raise FemRegionMeshError(f"{labels}: coupled meshing failed: {exc}") from exc
    finally:
        gmsh.option.setNumber("Mesh.MeshOnlyVisible", 0)
        gmsh.clear()
