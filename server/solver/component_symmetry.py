"""Infer full-driver completion and physical image orbits from moving skins."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from .beat_adapter.preflight import UnsupportedPhysics


SYMMETRY_FIELDS = frozenset({"symmetry_role", "fractional_symmetry_axes",
                             "surface_completion_factor", "physical_driver_orbit_count"})


class DriverSymmetryError(UnsupportedPhysics):
    """A geometry refusal with a stable admission code."""

    def __init__(self, message: str, *, code: str = "driver_symmetry_unsupported"):
        self.code = code
        super().__init__(message)


def infer_component_symmetry(
    meshes_m: Mapping[str, Sequence[tuple[np.ndarray, np.ndarray]]],
    motion_axis: Sequence[float], active_axes: tuple[int, ...] = (), *,
    channel_id: str = "unknown", tolerance_m: float = 1e-9,
) -> dict:
    """Judge the representative by welded edges or overlapping motion footprints.

    Mesh-local perimeters remain separate for symmetry completion; front/rear
    skins must not cancel those cuts when they describe the same diaphragm.
    """
    if len(set(active_axes)) != len(active_axes) or any(a not in (0, 1) for a in active_axes):
        raise DriverSymmetryError("Driver symmetry supports only x0 and y0 cut planes")
    axis = np.asarray(motion_axis, dtype=float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or not np.any(axis):
        raise DriverSymmetryError(f"Channel {channel_id!r}: motion_axis must be finite and nonzero")
    axis = axis / np.max(np.abs(axis))
    axis /= np.linalg.norm(axis)
    cuts = []
    patches = []
    for mesh, groups in meshes_m.items():
        if not groups or not any(len(triangles) for _, triangles in groups):
            continue
        corners = np.concatenate([triangles for _, triangles in groups])
        points, inverse = np.unique(np.rint(corners.reshape(-1, 3) / tolerance_m),
                                    axis=0, return_inverse=True)
        points *= tolerance_m
        faces = inverse.reshape(-1, 3)
        owners: dict[tuple[int, int], list[int]] = {}
        neighbours = [set() for _ in faces]
        for index, face in enumerate(faces):
            for a, b in zip(face, np.roll(face, -1)):
                edge = tuple(sorted((int(a), int(b))))
                for other in owners.setdefault(edge, []):
                    neighbours[index].add(other)
                    neighbours[other].add(index)
                owners[edge].append(index)
        if any(len(indices) > 2 for indices in owners.values()):
            raise DriverSymmetryError(f"Channel {channel_id!r}: moving surface union is nonmanifold")
        perimeter = [(edge, indices[0]) for edge, indices in owners.items() if len(indices) == 1]
        remaining = set(range(len(faces)))
        mesh_cuts = []
        while remaining:
            pending = [min(remaining)]
            patch = set(pending)
            remaining.difference_update(patch)
            while pending:
                adjacent = neighbours[pending.pop()] & remaining
                remaining.difference_update(adjacent)
                patch.update(adjacent)
                pending.extend(adjacent)
            edges = [edge for edge, owner in perimeter if owner in patch]
            patches.append((corners[sorted(patch)], {edge for edge, indices in owners.items()
                                                       if any(i in patch for i in indices)}, points))
            mesh_cuts.append(tuple(a for a in active_axes if any(
                np.all(np.abs(points[list(edge), a]) <= tolerance_m) for edge in edges)))
        cuts.extend(mesh_cuts)
    if patches:
        # Compare all meshes, including separate FEM chambers and BEM skins.
        parent = list(range(len(patches)))
        def root(i):
            while parent[i] != i:
                i = parent[i]
            return i
        edge_sets = [{tuple(sorted(tuple(point) for point in points[list(edge)]))
                      for edge in edges} for _, edges, points in patches]
        footprints = {}
        helper = np.eye(3)[np.argmin(np.abs(axis))]
        u = np.cross(axis, helper)
        u /= np.linalg.norm(u)
        basis = np.array([u, np.cross(axis, u)]).T
        for i in range(len(patches)):
            for j in range(i):
                if root(i) == root(j):
                    continue
                connected = bool(edge_sets[i] & edge_sets[j])
                if not connected:
                    for k in (i, j):
                        if k not in footprints:
                            footprints[k] = _footprint(patches[k][0] @ basis)
                    connected = _footprints_overlap(footprints[i], footprints[j])
                if connected:
                    parent[root(i)] = root(j)
        count = len({root(i) for i in range(len(patches))})
        if count > 1:
            raise DriverSymmetryError(
                f"Channel {channel_id!r}: its moving faces form {count} separate drivers; "
                "give each driver its own drive channel, or, if these are the two faces of one diaphragm, "
                "make them overlap along the motion axis / paint the rim as moving",
                code="driver_disconnected_patches")
    if not cuts:
        raise DriverSymmetryError(f"Channel {channel_id!r}: driver has no moving triangles")
    if len(set(cuts)) != 1:
        raise DriverSymmetryError(f"Channel {channel_id!r}: moving surfaces in different meshes imply different "
                         "symmetry cuts; split them into separate driver channels")
    fractional = cuts[0]
    for a in fractional:
        if abs(axis[a]) > 1e-8:
            raise DriverSymmetryError(f"Channel {channel_id!r}: motion_axis must lie in cutting plane "
                             f"{'xy'[a]}0 for this physical driver; prepare with Force full domain")
    completion = 2 ** len(fractional)
    return {"symmetry_role": "fractional_driver" if fractional else "complete_representative",
            "fractional_symmetry_axes": ['xy'[a] for a in fractional],
            "surface_completion_factor": completion,
            "physical_driver_orbit_count": 2 ** len(active_axes) // completion}


def _polygon_area(polygon):
    if len(polygon) < 3:
        return 0.
    points = np.asarray(polygon)
    x, y = (points - points[0]).T
    return abs(float(x @ np.roll(y, -1) - y @ np.roll(x, -1))) / 2


def _clip(polygon, a, b, inside=True):
    """Clip a convex polygon to one oriented half-plane."""
    if not len(polygon):
        return []
    edge = b - a
    def side(p):
        d = p - a
        return (edge[0] * d[1] - edge[1] * d[0]) * (1 if inside else -1)
    result = []
    for p, q in zip(polygon, np.roll(polygon, -1, axis=0)):
        sp, sq = side(p), side(q)
        if sp >= 0:
            result.append(p)
        if (sp >= 0) != (sq >= 0):
            result.append(p + (q - p) * (sp / (sp - sq)))
    return result


def _footprint(triangles):
    """Disjoint convex pieces of the projected union, including folded skins.

    Subtract earlier triangles from each new triangle. Bounds skip unrelated
    faces; clipping preserves holes (a coaxial annulus is not a filled disc).
    """
    pieces, previous = [], [None] * len(triangles)
    lower, upper = triangles.min(axis=1), triangles.max(axis=1)
    for i, triangle in enumerate(triangles):
        if _polygon_area(triangle) == 0:
            continue
        e, f = triangle[1] - triangle[0], triangle[2] - triangle[0]
        if e[0] * f[1] - e[1] * f[0] < 0:
            triangle = triangle[::-1]
        fragments = [triangle]
        candidates = np.flatnonzero(((lower[:i] < upper[i]) & (upper[:i] > lower[i])).all(axis=1))
        for j in candidates:
            clipper = previous[j]
            if clipper is None:
                continue
            remainder = []
            for fragment in fragments:
                for a, b in zip(clipper, np.roll(clipper, -1, axis=0)):
                    outside = _clip(fragment, a, b, False)
                    if _polygon_area(outside) > 0:
                        remainder.append(np.asarray(outside))
                    fragment = _clip(fragment, a, b)
                    if len(fragment) == 0:
                        break
            fragments = remainder
            if not fragments:
                break
        pieces.extend(fragments)
        previous[i] = triangle
    area = sum(_polygon_area(piece) for piece in pieces)
    return pieces, area


def _footprints_overlap(first, second):
    a, area_a = first
    b, area_b = second
    threshold = .5 * min(area_a, area_b)
    if threshold <= 0:
        return False
    lower = np.array([p.min(axis=0) for p in b])
    upper = np.array([p.max(axis=0) for p in b])
    overlap = 0.
    for polygon in a:
        candidates = np.flatnonzero(((lower < polygon.max(axis=0)) & (upper > polygon.min(axis=0))).all(axis=1))
        for j in candidates:
            fragment = polygon
            for p, q in zip(b[j], np.roll(b[j], -1, axis=0)):
                fragment = _clip(fragment, p, q)
                if len(fragment) == 0:
                    break
            overlap += _polygon_area(fragment)
            if overlap >= threshold * (1 - 1e-10):
                return True
    return False
