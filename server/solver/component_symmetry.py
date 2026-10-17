"""Infer full-driver completion and physical image orbits from moving skins."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import threading
from typing import NamedTuple

from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

import numpy as np

from .beat_adapter.preflight import UnsupportedPhysics


SYMMETRY_FIELDS = frozenset({"symmetry_role", "fractional_symmetry_axes",
                             "surface_completion_factor", "physical_driver_orbit_count"})


class DriverSymmetryError(UnsupportedPhysics):
    """A geometry refusal with a stable admission code."""

    def __init__(self, message: str, *, code: str = "driver_symmetry_unsupported"):
        self.code = code
        super().__init__(message)


_check = ContextVar("driver_geometry_check", default=lambda: None)
_verdicts = OrderedDict()
_cache_lock = threading.Lock()


class GeometryCheckCancelled(Exception):
    """Cooperative cancellation of admission geometry work."""


def check_geometry():
    _check.get()()


@contextmanager
def geometry_cancellation(check):
    token = _check.set(check)
    try:
        yield
    finally:
        _check.reset(token)


def _content_key(meshes, axis, axes, tolerance, channel):
    # Mesh/group partitions matter; artifact names and vertex indices do not.
    partitions = []
    for groups in meshes.values():
        hashes = []
        for _, triangles in groups:
            check_geometry()
            hashes.append(hashlib.sha256(np.asarray(triangles, dtype="<f8").tobytes()).digest())
        partitions.append(tuple(hashes))
    return (tuple(sorted(partitions)), tuple(axis), tuple(axes), tolerance, channel)


def _normalised(meshes, axes, tolerance):
    """One coordinate form for the cache key and the inference alike.

    Admission and compilation snap near-plane vertices with their own
    tolerances, and image transforms turn -0 into +0. Snap again on the
    active cut planes and drop the sign of zero, so equal geometry hashes equal.
    """
    result = {}
    for mesh, groups in meshes.items():
        normalised = []
        for faces, triangles in groups:
            triangles = np.array(triangles, dtype=float) + 0.  # -0 -> +0
            for axis in axes:
                if axis in (0, 1):
                    plane = triangles[..., axis]
                    plane[np.abs(plane) <= tolerance] = 0.
            normalised.append((faces, triangles))
        result[mesh] = normalised
    return result


def infer_component_symmetry(meshes_m, motion_axis, active_axes=(), *,
                             channel_id="unknown", tolerance_m=1e-9):
    meshes_m = _normalised(meshes_m, active_axes, tolerance_m)
    key = _content_key(meshes_m, motion_axis, active_axes, tolerance_m, channel_id)
    check_geometry()
    with _cache_lock:
        cached = _verdicts.get(key)
        if cached is not None:
            _verdicts.move_to_end(key)
    if cached is None:
        try:
            cached = (True, _infer_component_symmetry(meshes_m, motion_axis, active_axes,
                        channel_id=channel_id, tolerance_m=tolerance_m))
        except DriverSymmetryError as exc:
            cached = (False, (str(exc), exc.code))
        check_geometry()
        with _cache_lock:
            _verdicts[key] = cached
            if len(_verdicts) > 128:
                _verdicts.popitem(last=False)
    if not cached[0]:
        raise DriverSymmetryError(cached[1][0], code=cached[1][1])
    return dict(cached[1], fractional_symmetry_axes=list(cached[1]["fractional_symmetry_axes"]))


def _infer_component_symmetry(
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
        points[points == 0] = 0.  # Numeric edge records must equate +0 and -0.
        faces = inverse.reshape(-1, 3)
        check_geometry()
        edges = np.sort(faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
        unique_edges, owners, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        if np.any(counts > 2):
            raise DriverSymmetryError(f"Channel {channel_id!r}: moving surface union is nonmanifold")
        order = np.argsort(owners, kind="stable")
        starts = np.cumsum(counts) - counts
        shared = starts[counts == 2]
        left, right = order[shared] // 3, order[shared + 1] // 3
        graph = coo_matrix((np.ones(len(left)), (left, right)), shape=(len(faces), len(faces)))
        count, labels = connected_components(graph, directed=False)
        perimeter = unique_edges[counts == 1]
        perimeter_labels = labels[order[starts[counts == 1]] // 3]
        mesh_cuts = []
        for label in range(count):
            check_geometry()
            patch = labels == label
            patch_edges = unique_edges[np.unique(owners.reshape(-1, 3)[patch])]
            boundary = perimeter[perimeter_labels == label]
            patches.append((corners[patch], patch_edges, points))
            mesh_cuts.append(tuple(a for a in active_axes if np.any(
                np.all(np.abs(points[boundary, a]) <= tolerance_m, axis=1))))
        cuts.extend(mesh_cuts)
    if patches:
        # Compare all meshes, including separate FEM chambers and BEM skins.
        parent = list(range(len(patches)))
        def root(i):
            while parent[i] != i:
                i = parent[i]
            return i
        edge_sets = {}
        footprints = {}
        projections = []
        for triangles, _, _ in patches:
            cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            signed = cross @ axis
            area = np.abs(signed).sum() / 2
            projections.append((signed.sum(), 2 * np.sqrt(area / np.pi)))
        helper = np.eye(3)[np.argmin(np.abs(axis))]
        u = np.cross(axis, helper)
        u /= np.linalg.norm(u)
        basis = np.array([u, np.cross(axis, u)]).T
        for i in range(len(patches)):
            for j in range(i):
                if root(i) == root(j):
                    continue
                connected = False
                check_geometry()
                sign_i, size_i = projections[i]
                sign_j, size_j = projections[j]
                # Only nearby skins of one diaphragm may merge by footprint;
                # opposed drivers can share a footprint across an air gap.
                if sign_i * sign_j < 0:
                    for k in (i, j):
                        if k not in footprints:
                            triangles = patches[k][0]
                            footprints[k] = _footprint(triangles @ basis, triangles @ axis)
                    connected = _footprints_overlap(
                        footprints[i], footprints[j],
                        max_gap=max(.005, .05 * min(size_i, size_j)) + tolerance_m)
                if not connected:
                    for k in (i, j):
                        if k not in edge_sets:
                            _, edges, points = patches[k]
                            records = np.ascontiguousarray(points[edges]).reshape(len(edges), -1).view("V48").ravel()
                            edge_sets[k] = set(map(bytes, records))
                    connected = bool(edge_sets[i] & edge_sets[j])
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


class _Footprint(NamedTuple):
    triangles: np.ndarray
    area: float
    coverage: np.ndarray
    origin: np.ndarray
    cell: np.ndarray
    shape: np.ndarray
    axial: np.ndarray | None = None
    heights: np.ndarray | None = None
    centre: np.ndarray | None = None
    frame: np.ndarray | None = None

    def local(self, triangles):
        """Express common-frame triangles in this footprint's raster frame."""
        return triangles if self.frame is None else (triangles - self.centre) @ self.frame


# Cells along each side of a footprint's raster frame. Cells are anisotropic,
# so a thin strip's short side is resolved as finely as its long side; the
# budget stays FOOTPRINT_CELLS**2 cells whatever the aspect ratio.
FOOTPRINT_CELLS = 256


def _footprint(triangles, axial=None):
    """Area-weighted projected union on a grid independent of tessellation."""
    triangles = np.asarray(triangles, float)
    edges = triangles[:, 1:] - triangles[:, :1]
    area = np.abs(edges[:, 0, 0] * edges[:, 1, 1] - edges[:, 0, 1] * edges[:, 1, 0]) / 2
    if axial is not None:
        axial = np.asarray(axial, float)[area > 0]
    triangles = triangles[area > 0]
    if not len(triangles):
        return _Footprint(triangles, 0., np.zeros(1, bool), np.zeros(2), np.ones(2), np.ones(2, int))
    centre = frame = None
    local = triangles
    extent = np.ptp(triangles.reshape(-1, 2), axis=0)
    # A thin strip lying diagonally has a square bounding box; rasterise it in
    # its principal frame when that frame's box is much tighter.
    vertices = triangles.reshape(-1, 2)
    middle = vertices.mean(axis=0)
    _, basis = np.linalg.eigh((vertices - middle).T @ (vertices - middle))
    rotated = (triangles - middle) @ basis
    rotated_extent = np.ptp(rotated.reshape(-1, 2), axis=0)
    if np.prod(rotated_extent) < .5 * np.prod(extent):
        centre, frame, local, extent = middle, basis, rotated, rotated_extent
    origin = local.min(axis=(0, 1))
    cell = np.where(extent > 0, extent, extent.max()) / FOOTPRINT_CELLS
    shape = np.maximum(1, np.ceil(extent / cell - 1e-9).astype(int))
    heights = None
    if axial is None:
        coverage = _coverage(local, origin, cell, shape)
    else:
        heights = _coverage(local, origin, cell, shape, axial)
        coverage = np.isfinite(heights)
    return _Footprint(triangles, float(coverage.sum() * np.prod(cell)), coverage, origin, cell, shape,
                      axial, heights, centre, frame)


def _coverage(triangles, origin, cell, shape, axial=None):
    """Vectorised cell-centre coverage, with bounded cancellable chunks.

    Boolean union counts folded surfaces once and retains annular holes. Cell
    area is common to both skins, so counts give area-weighted overlap.
    With axial vertex coordinates, return interpolated cell heights instead,
    leaving uncovered cells NaN.
    """
    covered = (np.zeros(shape[0] * shape[1], bool) if axial is None
               else np.full(shape[0] * shape[1], np.nan))
    triangles = (triangles - origin) / cell
    lower = np.maximum(0, np.ceil(triangles.min(axis=1) - .5).astype(int))
    upper = np.minimum(np.array(shape) - 1, np.floor(triangles.max(axis=1) - .5).astype(int))
    widths = np.maximum(0, upper - lower + 1)
    work = widths.prod(axis=1)
    cumulative = np.concatenate([[0], np.cumsum(work)])
    start = 0
    while start < len(triangles):
        check_geometry()
        end = min(len(triangles), start + 2048,
                  int(np.searchsorted(cumulative, cumulative[start] + 131072, side="right")) - 1)
        end = max(start + 1, end)
        block, low = triangles[start:end], lower[start:end]
        block_axial = None if axial is None else axial[start:end]
        spans, counts = widths[start:end], work[start:end]
        start = end
        ids = np.repeat(np.arange(len(block)), counts)
        if not len(ids):
            continue
        offset = np.arange(len(ids)) - np.repeat(np.cumsum(counts) - counts, counts)
        x = low[ids, 0] + offset % spans[ids, 0]
        y = low[ids, 1] + offset // spans[ids, 0]
        points = np.column_stack([x + .5, y + .5])
        corner = block[ids]
        edges = np.roll(corner, -1, axis=1) - corner
        delta = points[:, None] - corner
        side = edges[:, :, 0] * delta[:, :, 1] - edges[:, :, 1] * delta[:, :, 0]
        inside = (side >= -1e-10).all(axis=1) | (side <= 1e-10).all(axis=1)
        cells = x[inside] + shape[0] * y[inside]
        if axial is None:
            covered[cells] = True
        else:
            # Edge signs give the opposite vertices' barycentric weights.
            weights = side[inside][:, [1, 2, 0]]
            weights /= weights.sum(axis=1, keepdims=True)
            covered[cells] = (weights * block_axial[ids[inside]]).sum(axis=1)
    return covered


def _footprints_overlap(first, second, *, max_gap=None):
    """Require shared area and, for skins, a robust gap on that same grid."""
    a, b = first.triangles, second.triangles
    if min(first.area, second.area) <= 0:
        return False
    lower = np.maximum(a.min(axis=(0, 1)), b.min(axis=(0, 1)))
    upper = np.minimum(a.max(axis=(0, 1)), b.max(axis=(0, 1)))
    if np.any(upper <= lower):
        return False
    # Rasterise the larger footprint onto the smaller footprint's grid. Both
    # now use the same cell area; folded triangles never inflate union area.
    smaller, larger = (first, second) if first.area <= second.area else (second, first)
    other = _coverage(smaller.local(larger.triangles), smaller.origin, smaller.cell, smaller.shape,
                      larger.axial if max_gap is not None else None)
    shared = smaller.coverage & (other if max_gap is None else np.isfinite(other))
    if np.count_nonzero(shared) < .5 * smaller.coverage.sum():
        return False
    # Front-only surrounds must not bias the gap between the shared skins.
    # The upper quartile also rejects clamshells whose rims nearly touch.
    return max_gap is None or bool(np.percentile(np.abs(smaller.heights[shared] - other[shared]), 75) <= max_gap)
