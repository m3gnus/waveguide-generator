"""
Clean surface .msh files for BEMPP workflows.

1) Merges coincident (or near-coincident) vertices within a tolerance.
2) Rebuilds triangle connectivity.
3) Removes collapsed and duplicate triangles.
4) Removes unused vertices.
5) Reports topology stats before/after (boundary/open edges, non-manifold edges, etc.).

Notes:
- Targets triangle surface meshes (common for BEM boundary meshes).
- Preserves triangle physical tags (gmsh:physical) when present.
- If true geometric holes exist, they will remain open; this script only stitches seams
  caused by duplicated/near-coincident vertices.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import meshio
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MeshStats:
    vertices: int
    triangles: int
    boundary_edges: int
    nonmanifold_edges: int
    duplicate_faces: int
    degenerate_faces: int
    components: int


def _find_triangle_block(mesh: meshio.Mesh) -> Tuple[str, np.ndarray]:
    cells_dict = mesh.cells_dict
    if "triangle" in cells_dict:
        return "triangle", np.asarray(cells_dict["triangle"], dtype=np.int64)
    if "triangle3" in cells_dict:
        return "triangle3", np.asarray(cells_dict["triangle3"], dtype=np.int64)
    raise ValueError("No triangle/triangle3 cell block found in mesh.")


def _extract_triangle_cell_data(mesh: meshio.Mesh, tri_key: str) -> Dict[str, np.ndarray]:
    out: Dict[str, np.ndarray] = {}
    for data_name, by_cell_type in mesh.cell_data_dict.items():
        if tri_key in by_cell_type:
            out[data_name] = np.asarray(by_cell_type[tri_key])
    return out


def _edge_counts(triangles: np.ndarray) -> Dict[Tuple[int, int], int]:
    counts: Dict[Tuple[int, int], int] = {}
    for a, b, c in triangles:
        for u, v in ((a, b), (b, c), (c, a)):
            if u > v:
                u, v = v, u
            key = (int(u), int(v))
            counts[key] = counts.get(key, 0) + 1
    return counts


def _connected_components(triangles: np.ndarray) -> int:
    if len(triangles) == 0:
        return 0

    edge_to_faces: Dict[Tuple[int, int], List[int]] = {}
    for face_index, (a, b, c) in enumerate(triangles):
        for u, v in ((a, b), (b, c), (c, a)):
            if u > v:
                u, v = v, u
            edge_to_faces.setdefault((int(u), int(v)), []).append(face_index)

    adjacency: List[set] = [set() for _ in range(len(triangles))]
    for face_ids in edge_to_faces.values():
        if len(face_ids) < 2:
            continue
        for i in range(len(face_ids)):
            for j in range(i + 1, len(face_ids)):
                f0 = face_ids[i]
                f1 = face_ids[j]
                adjacency[f0].add(f1)
                adjacency[f1].add(f0)

    seen = np.zeros(len(triangles), dtype=bool)
    components = 0
    for start in range(len(triangles)):
        if seen[start]:
            continue
        components += 1
        stack = [start]
        seen[start] = True
        while stack:
            node = stack.pop()
            for nxt in adjacency[node]:
                if not seen[nxt]:
                    seen[nxt] = True
                    stack.append(nxt)

    return components


def _degenerate_mask(points: np.ndarray, triangles: np.ndarray, area_tol: float) -> np.ndarray:
    v0 = points[triangles[:, 0]]
    v1 = points[triangles[:, 1]]
    v2 = points[triangles[:, 2]]

    repeated_vertex = (
        (triangles[:, 0] == triangles[:, 1])
        | (triangles[:, 1] == triangles[:, 2])
        | (triangles[:, 0] == triangles[:, 2])
    )
    area2 = np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)  # 2 * area
    tiny_area = area2 <= (2.0 * area_tol)
    return repeated_vertex | tiny_area


def mesh_stats(points: np.ndarray, triangles: np.ndarray, area_tol: float = 0.0) -> MeshStats:
    """Compute topology statistics for a triangle mesh."""
    deg_mask = _degenerate_mask(points, triangles, area_tol)

    sorted_faces = np.sort(triangles, axis=1)
    unique_faces = {tuple(row) for row in sorted_faces}
    duplicate_faces = len(sorted_faces) - len(unique_faces)

    edge_count = _edge_counts(triangles)
    boundary_edges = sum(1 for c in edge_count.values() if c == 1)
    nonmanifold_edges = sum(1 for c in edge_count.values() if c > 2)

    components = _connected_components(triangles)

    return MeshStats(
        vertices=len(points),
        triangles=len(triangles),
        boundary_edges=boundary_edges,
        nonmanifold_edges=nonmanifold_edges,
        duplicate_faces=duplicate_faces,
        degenerate_faces=int(np.sum(deg_mask)),
        components=components,
    )


def _spatial_hash_merge(points: np.ndarray, tol: float) -> np.ndarray:
    """
    Return representative index for each original point.
    Points within tol are merged (transitively) via local grid-neighbourhood checks.
    """
    if tol <= 0:
        return np.arange(len(points), dtype=np.int64)

    cell_size = tol
    inv = 1.0 / cell_size
    cell_coords = np.floor(points * inv).astype(np.int64)

    # Build cell -> point list
    grid: Dict[Tuple[int, int, int], List[int]] = {}
    for idx, c in enumerate(cell_coords):
        key = (int(c[0]), int(c[1]), int(c[2]))
        grid.setdefault(key, []).append(idx)

    parent = np.arange(len(points), dtype=np.int64)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    # Neighbour offsets in 3D grid
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]

    for key, idxs in grid.items():
        # Same-cell comparisons
        for i in range(len(idxs)):
            ii = idxs[i]
            pi = points[ii]
            for j in range(i + 1, len(idxs)):
                jj = idxs[j]
                if np.linalg.norm(pi - points[jj]) <= tol:
                    union(ii, jj)

        # Neighbour-cell comparisons (only forward keys to avoid duplicate work)
        kx, ky, kz = key
        for dx, dy, dz in offsets:
            nk = (kx + dx, ky + dy, kz + dz)
            if nk <= key:
                continue
            if nk not in grid:
                continue
            neigh = grid[nk]
            for ii in idxs:
                pi = points[ii]
                for jj in neigh:
                    if np.linalg.norm(pi - points[jj]) <= tol:
                        union(ii, jj)

    rep = np.array([find(i) for i in range(len(points))], dtype=np.int64)
    return rep


def _remove_duplicate_faces(
    triangles: np.ndarray, cell_data: Dict[str, np.ndarray]
) -> Tuple[np.ndarray, Dict[str, np.ndarray], int]:
    seen: Dict[Tuple[int, int, int], int] = {}
    keep_indices: List[int] = []
    removed = 0

    for idx, tri in enumerate(triangles):
        key = tuple(sorted((int(tri[0]), int(tri[1]), int(tri[2]))))
        if key in seen:
            removed += 1
            continue
        seen[key] = idx
        keep_indices.append(idx)

    keep = np.asarray(keep_indices, dtype=np.int64)
    triangles_out = triangles[keep]
    cell_data_out = {name: arr[keep] for name, arr in cell_data.items()}
    return triangles_out, cell_data_out, removed


def _compact_vertices(
    points: np.ndarray, triangles: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    used = np.unique(triangles.ravel())
    new_index = -np.ones(len(points), dtype=np.int64)
    new_index[used] = np.arange(len(used), dtype=np.int64)

    points_compact = points[used]
    triangles_compact = new_index[triangles]
    return points_compact, triangles_compact


def clean_mesh(
    mesh: meshio.Mesh,
    merge_tol: float = 1e-9,
    area_tol: float = 0.0,
) -> Tuple[meshio.Mesh, Dict[str, int], MeshStats, MeshStats]:
    """
    Clean a triangle surface mesh.

    Steps:
    1. Merge near-coincident vertices within merge_tol.
    2. Remove degenerate (collapsed) faces.
    3. Remove duplicate faces.
    4. Compact vertex list (remove orphaned vertices).

    Args:
        mesh: meshio.Mesh object to clean.
        merge_tol: Vertex merge tolerance in mesh units. Default 1e-9.
        area_tol: Minimum triangle area; smaller faces are removed. Default 0.0.

    Returns:
        (cleaned_mesh, changes_dict, stats_before, stats_after)
    """
    tri_key, triangles = _find_triangle_block(mesh)
    points = np.asarray(mesh.points, dtype=float)
    cell_data = _extract_triangle_cell_data(mesh, tri_key)

    stats_before = mesh_stats(points, triangles, area_tol)

    # 1) Merge near-coincident points
    rep = _spatial_hash_merge(points, merge_tol)
    unique_reps, inverse = np.unique(rep, return_inverse=True)
    points_merged = points[unique_reps]
    triangles_merged = inverse[triangles]

    merged_vertices = len(points) - len(points_merged)

    # 2) Remove degenerate faces
    deg_mask = _degenerate_mask(points_merged, triangles_merged, area_tol)
    keep = ~deg_mask
    triangles_clean = triangles_merged[keep]
    cell_data_clean = {name: arr[keep] for name, arr in cell_data.items()}
    removed_degenerate = int(np.sum(deg_mask))

    # 3) Remove duplicate faces
    triangles_clean, cell_data_clean, removed_duplicate = _remove_duplicate_faces(
        triangles_clean, cell_data_clean
    )

    # 4) Compact vertex list
    points_clean, triangles_clean = _compact_vertices(points_merged, triangles_clean)

    out_mesh = meshio.Mesh(
        points=points_clean,
        cells=[("triangle", triangles_clean)],
        point_data={},
        cell_data={name: [arr] for name, arr in cell_data_clean.items()},
        field_data=mesh.field_data,
    )

    stats_after = mesh_stats(points_clean, triangles_clean, area_tol)

    changes = {
        "merged_vertices": int(merged_vertices),
        "removed_degenerate_faces": int(removed_degenerate),
        "removed_duplicate_faces": int(removed_duplicate),
        "removed_unused_vertices": int(len(points_merged) - len(points_clean)),
    }

    logger.info(
        "[MeshCleaner] Before: %d verts, %d tris. "
        "After: %d verts, %d tris. "
        "Changes: %s",
        stats_before.vertices, stats_before.triangles,
        stats_after.vertices, stats_after.triangles,
        changes,
    )

    if stats_after.boundary_edges > 0:
        logger.warning(
            "[MeshCleaner] Mesh still has %d open edges after cleaning. "
            "This usually indicates real geometry holes (not just unstitched seams).",
            stats_after.boundary_edges,
        )

    return out_mesh, changes, stats_before, stats_after


def load_and_clean_msh(
    msh_path: str,
    merge_tol: float = 1e-9,
    area_tol: float = 0.0,
) -> Tuple[meshio.Mesh, Dict[str, int], MeshStats, MeshStats]:
    """
    Read a .msh file with meshio and clean it.

    Args:
        msh_path: Path to .msh file.
        merge_tol: Vertex merge tolerance (mesh units). Default 1e-9.
        area_tol: Minimum triangle area tolerance. Default 0.0.

    Returns:
        (cleaned_mesh, changes, stats_before, stats_after)
    """
    mesh = meshio.read(msh_path)
    return clean_mesh(mesh, merge_tol=merge_tol, area_tol=area_tol)


def extract_physical_tags(mesh: meshio.Mesh) -> Optional[np.ndarray]:
    """
    Extract physical tag array from a (possibly cleaned) meshio.Mesh.

    Returns:
        np.ndarray of int32 shape (num_triangles,) or None if not present.
    """
    tri_key, _ = _find_triangle_block(mesh)
    for key in mesh.cell_data_dict:
        if "gmsh:physical" in key and tri_key in mesh.cell_data_dict[key]:
            return np.asarray(mesh.cell_data_dict[key][tri_key], dtype=np.int32)
    return None
