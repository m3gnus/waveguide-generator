"""Spatially indexed closed-skin queries, shared by a plane and its stencil."""
from __future__ import annotations

import heapq

import numpy as np
from scipy.spatial import cKDTree


MAX_BIN_FACES = 128
MAX_WORK_PAIRS = 16384


class TriangleBins:
    """Adaptive AABB bins, split at triangle centroids above the face budget.

    Each triangle belongs to one leaf. Overlapping leaf bounds are queried
    separately, avoiding duplication of long triangles into many grid bins.
    """
    def __init__(self, triangles, dimensions, check, leaf_faces=MAX_BIN_FACES, oriented=False):
        xyz = triangles[:, :, :dimensions]
        lower, upper = xyz.min(axis=1), xyz.max(axis=1)
        centers = xyz.mean(axis=1)
        self.max_candidates = 0
        self.oriented_bounds = {}
        def build(indices):
            check()
            lo, hi = lower[indices].min(axis=0), upper[indices].max(axis=0)
            if len(indices) <= leaf_faces:
                self.max_candidates = max(self.max_candidates, len(indices))
                node = lo, hi, indices, None
            else:
                axis = np.argmax(np.ptp(centers[indices], axis=0))
                middle = len(indices) // 2
                order = np.argpartition(centers[indices, axis], middle)
                node = lo, hi, build(indices[order[:middle]]), build(indices[order[middle:]])
            if oriented:
                # A local box avoids the empty AABB corners around curved skins.
                # All triangle vertices are enclosed, so this remains a strict
                # lower bound for best-first nearest-triangle traversal.
                vertices = xyz[indices].reshape(-1, dimensions)
                origin = vertices.mean(axis=0)
                delta = vertices - origin
                _, basis = np.linalg.eigh(delta.T @ delta)
                projected = delta @ basis
                self.oriented_bounds[id(node)] = origin, basis, projected.min(axis=0), projected.max(axis=0)
            return node
        self.root = build(np.arange(len(triangles))) if len(triangles) else None

    def leaves(self, points, check, distances=None):
        pending = [(self.root, np.arange(len(points)))] if self.root is not None else []
        while pending:
            check()
            node, indices = pending.pop()
            lo, hi, left, right = node
            xyz = points[indices, :len(lo)]
            if distances is None:
                keep = ((xyz >= lo - 1e-9) & (xyz <= hi + 1e-9)).all(axis=1)
            else:
                delta = np.maximum(np.maximum(lo - xyz, xyz - hi), 0)
                keep = np.einsum("ij,ij->i", delta, delta) <= distances[indices] ** 2
            indices = indices[keep]
            if not len(indices):
                continue
            if right is None:
                yield indices, left
            else:
                pending.extend([(right, indices), (left, indices)])


def point_triangle_distances(points, triangles):
    """Exact distances for broadcastable point/triangle pairs, including edges."""
    a, b, c = np.moveaxis(triangles, -2, 0)
    e, f, delta = b - a, c - a, points - a
    normal = np.cross(e, f)
    area2 = np.sum(normal * normal, axis=-1)
    ee, ff, ef = (np.sum(x * y, axis=-1) for x, y in ((e, e), (f, f), (e, f)))
    de, df = np.sum(delta * e, axis=-1), np.sum(delta * f, axis=-1)
    denominator = np.maximum(area2, 1e-100)
    beta, gamma = (de * ff - df * ef) / denominator, (df * ee - de * ef) / denominator
    within = (area2 > 1e-24 * ee * ff) & (beta >= 0) & (gamma >= 0) & (beta + gamma <= 1)
    squared = np.where(within, np.sum(delta * normal, axis=-1) ** 2 / denominator, np.inf)
    for start, end in ((a, b), (b, c), (c, a)):
        edge = end - start
        t = np.clip(np.sum((points - start) * edge, axis=-1) / np.maximum(np.sum(edge * edge, axis=-1), 1e-100), 0, 1)
        residual = points - start - t[..., None] * edge
        squared = np.minimum(squared, np.sum(residual * residual, axis=-1))
    return np.sqrt(squared)


class SkinDistanceIndex:
    """Best-first exact nearest-triangle traversal, with small distance leaves."""
    def __init__(self, triangles, check=lambda: None):
        self.triangles = np.asarray(triangles)
        self.bins = TriangleBins(self.triangles, 3, check, leaf_faces=16, oriented=True)
        self.centroids = cKDTree(self.triangles.mean(axis=1))
        self.peak_pairs = 0
        self.pair_count = 0

    def query(self, points, check=lambda: None, *, return_sizes=False):
        points = np.asarray(points, float)
        if not len(self.triangles):
            distances = np.full(len(points), np.inf)
            return (distances, distances.copy()) if return_sizes else distances
        check()
        nearest = np.asarray(self.centroids.query(points)[1])
        distances = np.empty(len(points))
        for start in range(0, len(points), MAX_WORK_PAIRS):
            check()
            end = min(start + MAX_WORK_PAIRS, len(points))
            self.peak_pairs = max(self.peak_pairs, end - start)
            self.pair_count += end - start
            distances[start:end] = point_triangle_distances(points[start:end], self.triangles[nearest[start:end]])
        pending, serial = [], 0
        def push(node, indices):
            nonlocal serial
            origin, basis, lo, hi = self.bins.oriented_bounds[id(node)]
            xyz = (points[indices] - origin) @ basis
            delta = np.maximum(np.maximum(lo - xyz, xyz - hi), 0)
            bound = np.einsum("ij,ij->i", delta, delta)
            keep = bound <= distances[indices] ** 2
            if keep.any():
                serial += 1
                heapq.heappush(pending, (float(bound[keep].min()), serial, node, indices[keep], bound[keep]))
        if self.bins.root is not None and len(points):
            push(self.bins.root, np.arange(len(points)))
        while pending:
            check()
            _, _, node, indices, bound = heapq.heappop(pending)
            # Bounds are reconsidered after every nearer leaf. For each point,
            # traversal stops once the next box is farther than its best hit.
            indices = indices[bound <= distances[indices] ** 2]
            if not len(indices):
                continue
            _, _, left, right = node
            if right is not None:
                push(left, indices)
                push(right, indices)
                continue
            candidates = left
            chunk = max(1, MAX_WORK_PAIRS // len(candidates))
            for start in range(0, len(indices), chunk):
                check()
                selected = indices[start:start + chunk]
                self.peak_pairs = max(self.peak_pairs, len(selected) * len(candidates))
                self.pair_count += len(selected) * len(candidates)
                exact = point_triangle_distances(points[selected, None], self.triangles[candidates][None])
                closest = exact.argmin(axis=1)
                best = exact[np.arange(len(selected)), closest]
                improved = best < distances[selected]
                nearest[selected[improved]] = candidates[closest[improved]]
                distances[selected] = np.minimum(distances[selected], best)
        if return_sizes:
            triangles = self.triangles[nearest]
            sizes = np.linalg.norm(triangles - np.roll(triangles, -1, axis=1), axis=2).max(axis=1)
            return distances, sizes
        return distances


def skin_triangles(mesh, symmetry):
    signs = [np.ones(3)]
    for axis in ((0,) if symmetry == "x" else (0, 1) if symmetry == "xy" else ()):
        signs += [sign * np.where(np.arange(3) == axis, -1, 1) for sign in signs]
    return np.concatenate([mesh.points_m[mesh.faces] * sign for sign in signs])


class SkinRayIndex:
    """Adaptive projected AABB bins; exact triangle hits within each leaf.

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
        self.bins = TriangleBins(self.triangles, 2, check)
        self.pair_count = 0  # Deterministic scaling regression instrumentation.
        self.peak_pairs = 0

    def query(self, points, check=lambda: None):
        projected = np.asarray(points, float) @ self.basis
        inside = np.zeros(len(points), bool)
        clearance = np.full(len(points), np.inf)
        for indices, candidates in self.bins.leaves(projected, check):
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
            chunk = max(1, MAX_WORK_PAIRS // len(candidates))
            for start in range(0, len(indices), chunk):
                check()
                selected = indices[start:start + chunk]
                self.pair_count += len(selected) * len(candidates)
                self.peak_pairs = max(self.peak_pairs, len(selected) * len(candidates))
                delta = projected[selected, None, :2] - a[None, :, :2]
                s = (delta[:, :, 0] * f[:, 1] - delta[:, :, 1] * f[:, 0]) / det
                t = (e[:, 0] * delta[:, :, 1] - e[:, 1] * delta[:, :, 0]) / det
                hit = usable & (s >= -1e-10) & (t >= -1e-10) & (s + t <= 1 + 1e-10)
                distance = a[:, 2] + s * e[:, 2] + t * f[:, 2] - projected[selected, None, 2]
                clearance[selected] = np.minimum(clearance[selected], np.min(np.where(hit, np.abs(distance), np.inf), axis=1))
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
                inside[selected] ^= crossings % 2 == 1
        return inside | (clearance <= 1e-9), clearance


def _supercover_segment(blocked, a, b):
    """Traverse a clipped segment at every grid boundary, including corner ties."""
    delta = b - a
    size = np.array(blocked.shape[::-1])
    enter, leave = 0., 1.
    for axis in range(2):
        if delta[axis] == 0:
            if a[axis] < 0 or a[axis] > size[axis]:
                return
        else:
            ends = sorted((-a[axis] / delta[axis], (size[axis] - a[axis]) / delta[axis]))
            enter, leave = max(enter, ends[0]), min(leave, ends[1])
    if enter > leave:
        return
    stops = [enter, leave]
    for axis in range(2):
        if delta[axis] != 0:
            t = (np.arange(size[axis] + 1) - a[axis]) / delta[axis]
            stops.extend(t[(t > enter) & (t < leave)])
    stops = np.unique(stops)
    # Interval midpoints visit ordinary crossed cells. Boundary events visit
    # both neighbours (four at a corner), the closed-cell supercover convention.
    probes = np.concatenate([stops, (stops[:-1] + stops[1:]) / 2])
    for point in a + probes[:, None] * delta:
        lo = np.floor(point - 1e-10).astype(int)
        hi = np.floor(point + 1e-10).astype(int)
        lo, hi = np.maximum(lo, 0), np.minimum(hi, size - 1)
        if (lo <= hi).all():
            blocked[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1] = True


def _coplanar_cells(blocked, triangle):
    """Scan convex triangle/row-strip intersections, rather than its AABB."""
    first = max(0, int(np.floor(triangle[:, 1].min() - 1e-10)))
    last = min(blocked.shape[0] - 1, int(np.floor(triangle[:, 1].max() + 1e-10)))
    for row in range(first, last + 1):
        polygon = list(triangle)
        for boundary, sign in ((row, 1), (row + 1, -1)):
            clipped = []
            for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                a_in, b_in = sign * (a[1] - boundary) >= 0, sign * (b[1] - boundary) >= 0
                if a_in:
                    clipped.append(a)
                if a_in != b_in:
                    clipped.append(a + (b - a) * ((boundary - a[1]) / (b[1] - a[1])))
            polygon = clipped
        if polygon:
            x = np.asarray(polygon)[:, 0]
            lo, hi = max(0, int(np.floor(x.min() - 1e-10))), min(blocked.shape[1] - 1, int(np.floor(x.max() + 1e-10)))
            if lo <= hi:
                blocked[row, lo:hi + 1] = True


def crossing_cells(triangles, plane, rotation, check=lambda: None):
    """Supercover plane/skin cut segments and covered coplanar triangles."""
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
        if (np.abs(triangle[:, 2]) <= 1e-9).all():
            _coplanar_cells(blocked, (triangle[:, :2] + half) / spacing)
            continue
        hits = [p[:2] for p in triangle if abs(p[2]) <= 1e-9]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            p, q = triangle[a], triangle[b]
            if p[2] * q[2] < 0:
                hits.append((p + (q - p) * (-p[2] / (q[2] - p[2])))[:2])
        if not hits:
            continue
        _supercover_segment(blocked, (hits[0] + half) / spacing, (hits[-1] + half) / spacing)
    return blocked.ravel()
