"""Shared closed-surface topology and winding for mesh preparation and solvers."""

from __future__ import annotations

import numpy as np


def open_edges(triangles: np.ndarray) -> int:
    """Edges used by exactly one triangle: zero for a closed surface."""

    edges = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    _keys, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    return int(np.count_nonzero(counts == 1))


def orient_closed_surface(
    points: np.ndarray, triangles: np.ndarray, *, into_body: bool
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
