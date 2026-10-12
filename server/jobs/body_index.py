"""Spatially indexed closed-skin queries, shared by a plane and its stencil."""
from __future__ import annotations

import numpy as np


def skin_triangles(mesh, symmetry):
    signs = [np.ones(3)]
    for axis in ((0,) if symmetry == "x" else (0, 1) if symmetry == "xy" else ()):
        signs += [sign * np.where(np.arange(3) == axis, -1, 1) for sign in signs]
    return np.concatenate([mesh.points_m[mesh.faces] * sign for sign in signs])


class SkinRayIndex:
    """Uniform projected AABB bins; exact triangle hits within each bin.

    Rays share a direction. Work scales with local projected triangles rather
    than every point times every face. Half-open edge ownership handles triangulated
    faces and symmetry seams without counting a shared edge twice.
    """
    def __init__(self, triangles, direction, check=lambda: None):
        n = np.array(direction, dtype=float, copy=True)
        n /= np.linalg.norm(n)
        helper = np.eye(3)[np.argmin(np.abs(n))]
        u = np.cross(helper, n)
        u /= np.linalg.norm(u)
        self.basis = np.array([u, np.cross(n, u), n]).T
        self.triangles = triangles @ self.basis
        xy = self.triangles[:, :, :2]
        self.lo = xy.min(axis=(0, 1)) - 1e-9
        self.span = np.maximum(xy.max(axis=(0, 1)) - self.lo + 1e-9, 1e-9)
        self.resolution = min(128, max(1, int(np.sqrt(len(triangles)))))
        self.bins = {}
        lower = self._bins(xy.min(axis=1) - 1e-9)
        upper = self._bins(xy.max(axis=1) + 1e-9)
        for index, (a, b) in enumerate(zip(lower, upper)):
            if index % 512 == 0:
                check()
            for i in range(a[0], b[0] + 1):
                for j in range(a[1], b[1] + 1):
                    self.bins.setdefault(i * self.resolution + j, []).append(index)
        self.pair_count = 0  # Deterministic scaling regression instrumentation.

    def _bins(self, xy):
        return np.clip(((xy - self.lo) / self.span * self.resolution).astype(int), 0, self.resolution - 1)

    def query(self, points, check=lambda: None):
        projected = np.asarray(points, float) @ self.basis
        bins = self._bins(projected[:, :2])
        keys = bins[:, 0] * self.resolution + bins[:, 1]
        inside = np.zeros(len(points), bool)
        clearance = np.full(len(points), np.inf)
        order = np.argsort(keys)
        groups = np.split(order, np.flatnonzero(np.diff(keys[order])) + 1) if len(order) else []
        for indices in groups:
            check()
            candidates = self.bins.get(keys[indices[0]])
            if not candidates:
                continue
            a, b, c = self.triangles[candidates].transpose(1, 0, 2)
            e, f = b - a, c - a
            det = e[:, 0] * f[:, 1] - e[:, 1] * f[:, 0]
            # Normalise projected winding, then use rasterisation's half-open
            # edge rule. Tangent rays at shared vertices must not toggle parity.
            reverse = det < 0
            b, c = np.where(reverse[:, None], c, b), np.where(reverse[:, None], b, c)
            e, f = b - a, c - a
            det = np.abs(det)
            usable = det > 1e-24
            det = np.where(usable, det, 1)
            def top_left(edge):
                return (edge[:, 1] > 0) | ((edge[:, 1] == 0) & (edge[:, 0] < 0))
            edges = (top_left(a - c), top_left(b - a), top_left(c - b))
            for start in range(0, len(indices), 128):
                check()
                selected = indices[start:start + 128]
                self.pair_count += len(selected) * len(candidates)
                delta = projected[selected, None, :2] - a[None, :, :2]
                s = (delta[:, :, 0] * f[:, 1] - delta[:, :, 1] * f[:, 0]) / det
                t = (e[:, 0] * delta[:, :, 1] - e[:, 1] * delta[:, :, 0]) / det
                hit = usable & (s >= -1e-10) & (t >= -1e-10) & (s + t <= 1 + 1e-10)
                distance = a[:, 2] + s * e[:, 2] + t * f[:, 2] - projected[selected, None, 2]
                clearance[selected] = np.min(np.where(hit, np.abs(distance), np.inf), axis=1)
                ray_hit = np.broadcast_to(usable, s.shape).copy()
                for weight, inclusive in zip((s, t, 1 - s - t), edges):
                    ray_hit &= (weight > 1e-10) | ((np.abs(weight) <= 1e-10) & inclusive)
                if (~usable).any():
                    # A face parallel to the ray has no projected area, but a
                    # point on that face must still avoid the singular potential.
                    ep, fp = e[~usable], f[~usable]
                    delta3 = projected[selected, None] - a[None, ~usable]
                    normals = np.cross(ep, fp)
                    area2 = np.einsum("ij,ij->i", normals, normals)
                    ee, ff, ef = (np.einsum("ij,ij->i", x, y) for x, y in ((ep, ep), (fp, fp), (ep, fp)))
                    de = np.einsum("nij,ij->ni", delta3, ep)
                    df = np.einsum("nij,ij->ni", delta3, fp)
                    denominator = np.maximum(area2, 1e-100)
                    beta, gamma = (de * ff - df * ef) / denominator, (df * ee - de * ef) / denominator
                    on_face = ((area2 > 1e-40) & (beta >= -1e-10) & (gamma >= -1e-10)
                               & (beta + gamma <= 1 + 1e-10)
                               & (np.abs(np.einsum("nij,ij->ni", delta3, normals)) <= 1e-9 * np.sqrt(area2)))
                    clearance[selected[on_face.any(axis=1)]] = 0
                crossings = (ray_hit & (distance > 1e-9)).sum(axis=1)
                inside[selected] = (crossings % 2 == 1) | (clearance[selected] <= 1e-9)
        return inside, clearance


def crossing_cells(triangles, plane, rotation, check=lambda: None):
    """Conservatively rasterise plane/skin intersections into grid cells.

    Segment AABBs include diagonal crossings and sub-grid walls even when all
    four corners are air. Coplanar faces also block their covered cells.
    """
    origin = np.asarray(plane.origin_m) @ rotation.T
    u, v = np.asarray(plane.axis_u) @ rotation.T, np.asarray(plane.axis_v) @ rotation.T
    basis = np.array([u, v, np.cross(u, v)]).T
    xyz = (triangles - origin) @ basis
    z = xyz[:, :, 2]
    selected = xyz[(z.min(axis=1) <= 1e-9) & (z.max(axis=1) >= -1e-9)]
    blocked = np.zeros((plane.ny - 1, plane.nx - 1), bool)
    spacing = np.array([plane.width_m / (plane.nx - 1), plane.height_m / (plane.ny - 1)])
    half = np.array([plane.width_m, plane.height_m]) / 2
    for index, triangle in enumerate(selected):
        if index % 512 == 0:
            check()
        hits = [p[:2] for p in triangle if abs(p[2]) <= 1e-9]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            p, q = triangle[a], triangle[b]
            if p[2] * q[2] < 0:
                hits.append((p + (q - p) * (-p[2] / (q[2] - p[2])))[:2])
        if not hits:
            continue
        lo = np.floor((np.min(hits, axis=0) + half - 1e-9) / spacing).astype(int)
        hi = np.floor((np.max(hits, axis=0) + half + 1e-9) / spacing).astype(int)
        lo = np.maximum(lo, 0)
        hi = np.minimum(hi, [plane.nx - 2, plane.ny - 2])
        if (lo <= hi).all():
            blocked[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1] = True
    return blocked.ravel()
