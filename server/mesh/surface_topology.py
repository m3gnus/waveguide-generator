"""Shared closed-surface topology and winding for mesh preparation and solvers."""

from __future__ import annotations

from typing import NamedTuple, Sequence

from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

import numpy as np


def open_edges(triangles: np.ndarray) -> int:
    """Edges used by exactly one triangle: zero for a closed surface."""

    edges = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    _keys, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    return int(np.count_nonzero(counts == 1))


class SurfacePatches(NamedTuple):
    """Triangles grouped into patches that share an edge, with each one's rim."""

    count: int
    labels: np.ndarray
    edges: np.ndarray
    edge_owners: np.ndarray
    edge_counts: np.ndarray
    rim_edges: np.ndarray
    rim_labels: np.ndarray


def surface_patches(triangles: np.ndarray) -> SurfacePatches:
    """Split a surface at its open edges; an edge of three or more faces joins nothing."""

    edges = np.sort(triangles[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    unique_edges, owners, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    order = np.argsort(owners, kind="stable")
    starts = np.cumsum(counts) - counts
    shared = starts[counts == 2]
    left, right = order[shared] // 3, order[shared + 1] // 3
    graph = coo_matrix((np.ones(len(left)), (left, right)), shape=(len(triangles), len(triangles)))
    count, labels = connected_components(graph, directed=False)
    return SurfacePatches(
        count, labels, unique_edges, owners, counts,
        unique_edges[counts == 1], labels[order[starts[counts == 1]] // 3],
    )


def cutting_axes(
    points: np.ndarray, rim_edges: np.ndarray, axes: Sequence[int], tolerance: float
) -> tuple[int, ...]:
    """The coordinate planes through the origin that a patch's open rim lies along."""

    return tuple(
        axis for axis in axes
        if np.any(np.all(np.abs(points[rim_edges, axis]) <= tolerance, axis=1))
    )


def completion_factor(
    points: np.ndarray, triangles: np.ndarray, axes: Sequence[int], tolerance: float
) -> float:
    """How many times larger this surface is once the cut planes ``axes`` complete it.

    A patch with an open rim along a plane continues in its own mirror image
    there, so that plane doubles it; a patch clear of the plane is whole. The
    area weighting keeps a source that holds both kinds exact.
    """

    if not len(triangles):
        return 1.0
    corners = points[triangles]
    areas = 0.5 * np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
    )
    patches = surface_patches(triangles)
    whole = sum(
        float(areas[patches.labels == label].sum()) * 2 ** len(
            cutting_axes(points, patches.rim_edges[patches.rim_labels == label], axes, tolerance)
        )
        for label in range(patches.count)
    )
    return whole / float(areas.sum())


def orient_closed_surface(
    points: np.ndarray, triangles: np.ndarray, *, into_body: bool,
    symmetry_plane_axes: tuple[int, ...] = ()
) -> np.ndarray:
    """Return ``triangles`` wound consistently, every normal on one side.

    gmsh winds each surface's triangles by that surface's own OCC
    parametrisation, so a mesh assembled from several surfaces is not
    consistently oriented; a boundary-element operator needs it to be. The
    winding is propagated across shared edges, then each connected skin is
    flipped if needed so normals point into the enclosed body
    (``into_body=True``, outward from the exterior air) or out of it.
    Nested closed skins refuse: they do not all border unbounded air, and a
    sealed chamber needs an explicit modelling decision before coupling.
    """

    triangles = np.array(triangles, dtype=np.int64, copy=True)
    if symmetry_plane_axes:
        from server.mesh.integrity import mesh_integrity_report

        report = mesh_integrity_report(points * 1e-3, triangles,
                                       symmetry_plane_axes=symmetry_plane_axes)
        if report["off_plane_open_edge_count"] or report["nonmanifold_edge_count"]:
            raise ValueError("Reduced exterior has open edges away from symmetry planes or nonmanifold edges")
        # Complete only for winding/closure checks; return the original faces.
        # Plane vertices keep their ids, so this performs no coordinate welding.
        if any(np.any(np.all(np.abs(points[triangles, axis]) <= 1e-5, axis=1))
               for axis in symmetry_plane_axes):
            raise ValueError("Reduced exterior must be open on symmetry planes, without plane caps")
        count = len(triangles)
        completed_points = np.asarray(points).copy()
        completed = triangles.copy()
        for axis in symmetry_plane_axes:
            off_plane = np.abs(completed_points[:, axis]) > 1e-5
            mirrored = completed_points[off_plane].copy()
            mirrored[:, axis] *= -1
            mapping = np.arange(len(completed_points))
            mapping[off_plane] = len(completed_points) + np.arange(len(mirrored))
            completed_points = np.concatenate([completed_points, mirrored])
            completed = np.concatenate([completed, mapping[completed[:, [0, 2, 1]]]])
        if open_edges(completed):
            raise ValueError("Reduced exterior does not close under symmetry reflection")
        return orient_closed_surface(completed_points, completed, into_body=into_body)[:count]
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for face, (a, b, c) in enumerate(triangles.tolist()):
        for u, v in ((a, b), (b, c), (c, a)):
            edge_faces.setdefault((min(u, v), max(u, v)), []).append(face)
    visited = np.zeros(len(triangles), dtype=bool)
    components = []
    for seed in range(len(triangles)):
        if visited[seed]:
            continue
        visited[seed] = True
        component = [seed]
        stack = [seed]
        while stack:
            face = stack.pop()
            a, b, c = triangles[face].tolist()
            for u, v in ((a, b), (b, c), (c, a)):
                for other in edge_faces[(min(u, v), max(u, v))]:
                    if other == face or visited[other]:
                        continue
                    x, y, z = triangles[other].tolist()
                    # A consistent neighbour traverses the shared edge the other way.
                    if (u, v) in ((x, y), (y, z), (z, x)):
                        triangles[other] = [x, z, y]
                    visited[other] = True
                    stack.append(other)
                    component.append(other)
        # Orient each connected skin separately: unrelated bodies can arrive
        # with different OCC seed windings. Recentre for numerical stability.
        rows = np.asarray(component, dtype=np.int64)
        components.append(rows)
        corners = points[triangles[rows]]
        corners = corners - corners[0, 0]
        signed_volume = (
            np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6
        )
        outward = signed_volume > 0
        if outward == into_body:
            triangles[rows] = triangles[rows][:, [0, 2, 1]]
    for rows in components:
        inner = points[triangles[rows]]
        for enclosing in components:
            if enclosing is rows:
                continue
            outer = points[triangles[enclosing]]
            if np.any(inner.min(axis=(0, 1)) < outer.min(axis=(0, 1))) or np.any(
                inner.max(axis=(0, 1)) > outer.max(axis=(0, 1))
            ):
                continue
            # A node shared at a concave vertex has an ambiguous solid angle.
            # Probe inside a triangle, preferably one with no shared vertices.
            separate = ~np.isin(triangles[rows], triangles[enclosing]).any(axis=1)
            probe = inner[np.flatnonzero(separate)[0] if separate.any() else 0].mean(axis=0)
            # The signed solid angle is +/-4*pi inside a closed skin, zero
            # outside. Unlike a whole-component centroid this handles concave skins.
            a, b, c = (outer[:, index] - probe for index in range(3))
            la, lb, lc = (np.linalg.norm(v, axis=1) for v in (a, b, c))
            numerator = np.einsum("ij,ij->i", a, np.cross(b, c))
            denominator = (la * lb * lc + np.einsum("ij,ij->i", a, b) * lc
                           + np.einsum("ij,ij->i", b, c) * la
                           + np.einsum("ij,ij->i", c, a) * lb)
            angle = 2 * np.arctan2(numerator, denominator).sum()
            if abs(angle) > 2 * np.pi:
                area = np.linalg.norm(
                    np.cross(inner[:, 1] - inner[:, 0], inner[:, 2] - inner[:, 0]), axis=1
                ).sum() / 2
                location = inner.mean(axis=(0, 1))
                raise ValueError(
                    "Nested closed skin component (sealed chamber) at "
                    f"{location.tolist()} mm, area {area:g} mm2, is enclosed by another "
                    "component and does not border unbounded exterior air; declare or "
                    "remove the sealed chamber before coupling."
                )
    return triangles
