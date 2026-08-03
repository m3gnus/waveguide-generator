"""Topology integrity reporting for canonical triangular solve meshes.

The report makes the open-edge guard used by v1 Metal/BEMPP adapters explicit;
see ``server/solver/metal_solver.py:232-252`` and
``server/solver/bempp_solver.py:229-242`` in v1.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np


def mesh_integrity_report(vertices: Any, triangles: Any) -> dict[str, Any]:
    """Count boundary, non-manifold, degenerate, and invalid triangle edges."""

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
            "degenerate_triangle_count": 0,
            "invalid_index_triangle_count": 0,
            "open_edges_sample": [],
        }

    edge_counts: Counter[tuple[int, int]] = Counter()
    degenerate = 0
    invalid = 0
    for face in faces:
        a, b, c = (int(face[0]), int(face[1]), int(face[2]))
        if min(a, b, c) < 0 or max(a, b, c) >= len(points):
            invalid += 1
            continue
        if len({a, b, c}) < 3:
            degenerate += 1
            continue
        cross = np.cross(points[b] - points[a], points[c] - points[a])
        if not np.all(np.isfinite(cross)) or float(np.linalg.norm(cross)) <= 1.0e-15:
            degenerate += 1
        for first, second in ((a, b), (b, c), (c, a)):
            edge_counts[(min(first, second), max(first, second))] += 1

    open_edges = sorted(edge for edge, count in edge_counts.items() if count == 1)
    nonmanifold = sum(1 for count in edge_counts.values() if count > 2)
    valid = invalid == 0 and degenerate == 0 and nonmanifold == 0
    return {
        "valid": valid,
        "is_watertight": valid and not open_edges,
        "open_edge_count": len(open_edges),
        "nonmanifold_edge_count": nonmanifold,
        "degenerate_triangle_count": degenerate,
        "invalid_index_triangle_count": invalid,
        "open_edges_sample": [list(edge) for edge in open_edges[:20]],
    }


__all__ = ["mesh_integrity_report"]
