"""Mesh loading and validated structure-of-arrays geometry buffers.

Array conventions mirror ``hornlab-metal-bem/docs/native-ipc.md`` §"Array
Conventions" (lines 139-161) and the geometry manifest at lines 48-55, with one
deliberate deviation: **every float array here is float64, not float32.** The
Metal IPC boundary is declared ``precision: complex64`` (native-ipc.md:19); this
backend is the fp64 reference, so the same shapes carry double precision.

    vertices          (3, n_vertices)   float64
    triangles         (3, n_triangles)  int32, zero-based
    physical_tags     (n_triangles,)    int32
    p1_local2global   (n_triangles, 3)  int32, zero-based
    areas             (n_triangles,)    float64
    normals           (3, n_triangles)  float64, unit length, outward

Index base is zero throughout (native-ipc.md:58-59).

Loading mirrors ``hornlab_bempp_bem.mesh.load_mesh`` so that the vertex and
element numbering produced here is identical to the bempp grid built from the
same ``.msh``: meshio read, optional scale, seam-vertex merge at 1e-9,
degenerate-triangle removal, then outward-winding validation by signed volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "MeshError",
    "SurfaceMesh",
    "build_surface_mesh",
    "load_msh",
]

DEFAULT_MERGE_TOL = 1e-9


class MeshError(ValueError):
    """Raised when a mesh cannot be loaded or fails validation."""


@dataclass(frozen=True)
class SurfaceMesh:
    """Validated SoA buffers for a triangulated surface.

    All arrays are C-contiguous. ``p1_local2global`` is the transposed
    connectivity (mirrors ``hornlab_metal_bem/mesh.py:259-263``), so the P1
    trial/test space has one DOF per vertex and the DP0 space one DOF per
    triangle.
    """

    vertices: NDArray[np.float64]        # (3, nv)
    triangles: NDArray[np.int32]         # (3, nt)
    physical_tags: NDArray[np.int32]     # (nt,)
    p1_local2global: NDArray[np.int32]   # (nt, 3)
    areas: NDArray[np.float64]           # (nt,)
    normals: NDArray[np.float64]         # (3, nt)

    @property
    def n_vertices(self) -> int:
        return int(self.vertices.shape[1])

    @property
    def n_triangles(self) -> int:
        return int(self.triangles.shape[1])

    @property
    def p1_dof_count(self) -> int:
        return self.n_vertices

    @property
    def dp0_dof_count(self) -> int:
        return self.n_triangles

    def triangle_vertices(self) -> NDArray[np.float64]:
        """Return the per-triangle vertex coordinates as ``(nt, 3, 3)``.

        Axis 1 is the local vertex (0, 1, 2); axis 2 is x/y/z.
        """
        return np.ascontiguousarray(
            self.vertices[:, self.triangles].transpose(2, 1, 0)
        )

    def tag_mask(self, tag: int) -> NDArray[np.bool_]:
        return self.physical_tags == int(tag)


def build_surface_mesh(
    vertices: NDArray[np.floating],
    triangles: NDArray[np.integer],
    physical_tags: NDArray[np.integer] | None = None,
) -> SurfaceMesh:
    """Derive and validate the SoA buffers from raw vertices/connectivity.

    ``vertices`` is ``(3, nv)`` and ``triangles`` is ``(3, nt)`` zero-based, i.e.
    the same orientation the native IPC layer uses.
    """
    verts = np.ascontiguousarray(vertices, dtype=np.float64)
    tris = np.ascontiguousarray(triangles, dtype=np.int32)

    if verts.ndim != 2 or verts.shape[0] != 3:
        raise MeshError(f"vertices must have shape (3, nv); got {verts.shape}")
    if tris.ndim != 2 or tris.shape[0] != 3:
        raise MeshError(f"triangles must have shape (3, nt); got {tris.shape}")

    n_vertices = verts.shape[1]
    n_triangles = tris.shape[1]
    if n_vertices == 0 or n_triangles == 0:
        raise MeshError("Mesh has no vertices or no triangles")
    if not np.all(np.isfinite(verts)):
        raise MeshError("Mesh vertices contain non-finite coordinates")
    if tris.min() < 0 or tris.max() >= n_vertices:
        raise MeshError(
            f"Triangle indices out of range [0, {n_vertices}): "
            f"min={int(tris.min())} max={int(tris.max())}"
        )

    if physical_tags is None:
        tags = np.ones(n_triangles, dtype=np.int32)
    else:
        tags = np.ascontiguousarray(physical_tags, dtype=np.int32).reshape(-1)
        if tags.shape[0] != n_triangles:
            raise MeshError(
                f"physical_tags has {tags.shape[0]} entries but the mesh has "
                f"{n_triangles} triangles"
            )

    p0 = verts[:, tris[0]]
    p1 = verts[:, tris[1]]
    p2 = verts[:, tris[2]]
    raw = np.cross((p1 - p0).T, (p2 - p0).T).T          # (3, nt); |raw| = 2*area
    mags = np.linalg.norm(raw, axis=0)
    if np.any(mags <= 0.0):
        bad = int(np.count_nonzero(mags <= 0.0))
        raise MeshError(f"Mesh contains {bad} degenerate (zero-area) triangles")

    areas = np.ascontiguousarray(0.5 * mags)
    normals = np.ascontiguousarray(raw / mags[None, :])
    local2global = np.ascontiguousarray(tris.T, dtype=np.int32)

    return SurfaceMesh(
        vertices=verts,
        triangles=tris,
        physical_tags=tags,
        p1_local2global=local2global,
        areas=areas,
        normals=normals,
    )


def load_msh(
    path: str | Path,
    *,
    scale: float = 1.0,
    merge_tol: float = DEFAULT_MERGE_TOL,
    repair_normals: bool = False,
) -> SurfaceMesh:
    """Load a gmsh ``.msh`` file into validated SoA buffers.

    Mirrors ``hornlab_bempp_bem.mesh.load_mesh`` so the resulting numbering
    matches the bempp grid built from the same file. ``meshio`` is used when it
    is installed; otherwise a built-in MSH 2.2 ASCII reader is used, so this
    backend stays pure NumPy/SciPy at runtime.
    """
    path = Path(path)
    if not path.exists():
        raise MeshError(f"Mesh file not found: {path}")

    verts, tris, tags = _read_mesh(path)
    verts = verts * float(scale)

    verts, tris = _merge_duplicate_vertices(verts, tris, merge_tol)
    tris, tags = _drop_degenerate_triangles(verts, tris, tags)
    tris = _orient_outward(verts, tris, repair=repair_normals)

    return build_surface_mesh(verts.T, tris.T, tags)


def _read_mesh(
    path: Path,
) -> tuple[NDArray[np.float64], NDArray[np.int32], NDArray[np.int32]]:
    """Read vertices, triangles and physical tags, preferring meshio."""
    try:
        import meshio
    except ImportError:
        return _read_msh22_ascii(path)

    mesh = meshio.read(str(path))
    tris = np.asarray(mesh.get_cells_type("triangle"), dtype=np.int32)
    if tris.size == 0:
        raise MeshError("No triangles found in mesh")
    verts = np.asarray(mesh.points, dtype=np.float64)
    try:
        tags = np.asarray(
            mesh.get_cell_data("gmsh:physical", "triangle"), dtype=np.int32
        )
    except (KeyError, ValueError) as exc:  # pragma: no cover - malformed input
        raise MeshError("Mesh file has no triangle physical-group tags") from exc
    return verts, tris, tags


def _read_msh22_ascii(
    path: Path,
) -> tuple[NDArray[np.float64], NDArray[np.int32], NDArray[np.int32]]:
    """Minimal gmsh MSH 2.2 ASCII reader (nodes + 3-node triangles).

    Produces the same vertex order, triangle order and physical tags as
    ``meshio.read`` for this format, so the two paths are interchangeable.
    """
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()

    fmt_at = _section_index(lines, "$MeshFormat")
    version = lines[fmt_at + 1].split()[0] if fmt_at is not None else "?"
    if not version.startswith("2."):
        raise MeshError(
            f"Built-in reader supports MSH 2.x ASCII only (file reports {version}). "
            "Install meshio to read this mesh."
        )

    nodes_at = _section_index(lines, "$Nodes")
    if nodes_at is None:
        raise MeshError("Mesh file has no $Nodes section")
    n_nodes = int(lines[nodes_at + 1])
    node_ids = np.empty(n_nodes, dtype=np.int64)
    coords = np.empty((n_nodes, 3), dtype=np.float64)
    for i in range(n_nodes):
        parts = lines[nodes_at + 2 + i].split()
        node_ids[i] = int(parts[0])
        coords[i] = (float(parts[1]), float(parts[2]), float(parts[3]))

    elems_at = _section_index(lines, "$Elements")
    if elems_at is None:
        raise MeshError("Mesh file has no $Elements section")
    n_elems = int(lines[elems_at + 1])

    tri_nodes: list[tuple[int, int, int]] = []
    tri_tags: list[int] = []
    for i in range(n_elems):
        parts = lines[elems_at + 2 + i].split()
        if int(parts[1]) != 2:  # gmsh element type 2 == 3-node triangle
            continue
        n_tags = int(parts[2])
        if n_tags < 1:
            raise MeshError("Mesh file has no triangle physical-group tags")
        tri_tags.append(int(parts[3]))
        base = 3 + n_tags
        tri_nodes.append((int(parts[base]), int(parts[base + 1]), int(parts[base + 2])))

    if not tri_nodes:
        raise MeshError("No triangles found in mesh")

    # Map gmsh node ids (1-based, possibly sparse) onto zero-based indices.
    order = np.argsort(node_ids, kind="stable")
    sorted_ids = node_ids[order]
    raw = np.asarray(tri_nodes, dtype=np.int64)
    pos = np.searchsorted(sorted_ids, raw)
    if np.any(pos >= sorted_ids.size) or np.any(sorted_ids[np.minimum(pos, sorted_ids.size - 1)] != raw):
        raise MeshError("Mesh references node ids that are not defined")
    tris = order[pos].astype(np.int32)

    return coords, tris, np.asarray(tri_tags, dtype=np.int32)


def _section_index(lines: list[str], header: str) -> int | None:
    for i, line in enumerate(lines):
        if line.strip() == header:
            return i
    return None


def _merge_duplicate_vertices(
    verts: NDArray[np.float64],
    tris: NDArray[np.int32],
    tol: float,
) -> tuple[NDArray[np.float64], NDArray[np.int32]]:
    """Weld coincident seam vertices, then compact unused ones.

    Canonical HornLab meshes arrive already welded, so this is a no-op for them
    (verified in the geometry test against the reference horn).
    """
    if tol <= 0.0 or verts.shape[0] == 0:
        return verts, tris

    from scipy.spatial import cKDTree

    tree = cKDTree(verts)
    pairs = tree.query_pairs(tol, output_type="ndarray")
    if pairs.size == 0:
        return verts, tris

    parent = np.arange(verts.shape[0], dtype=np.int64)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = int(parent[i])
        return int(i)

    for a, b in pairs:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    roots = np.array([find(i) for i in range(verts.shape[0])], dtype=np.int64)
    used, remap = np.unique(roots, return_inverse=True)
    return np.ascontiguousarray(verts[used]), np.ascontiguousarray(
        remap[tris].astype(np.int32)
    )


def _drop_degenerate_triangles(
    verts: NDArray[np.float64],
    tris: NDArray[np.int32],
    tags: NDArray[np.int32],
) -> tuple[NDArray[np.int32], NDArray[np.int32]]:
    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    mags = np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
    keep = mags > 0.0
    if np.all(keep):
        return tris, tags
    return (
        np.ascontiguousarray(tris[keep]),
        np.ascontiguousarray(tags[keep]),
    )


def _orient_outward(
    verts: NDArray[np.float64],
    tris: NDArray[np.int32],
    *,
    repair: bool,
) -> NDArray[np.int32]:
    """Validate outward winding on closed meshes (bempp mesh.py:215-242).

    The signed-volume indicator is only translation-invariant for a closed
    two-manifold, so open shells are left untouched.
    """
    if not _is_closed_two_manifold(tris):
        return tris

    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    signed = float(np.sum(p0 * np.cross(p1, p2)))
    if signed >= 0.0:
        return tris
    if repair:
        tris = tris.copy()
        tris[:, [1, 2]] = tris[:, [2, 1]]
        return tris
    raise MeshError(
        "Mesh triangle winding appears inward (signed volume negative). "
        "Pass repair_normals=True only for external-mesh compatibility."
    )


def _is_closed_two_manifold(tris: NDArray[np.int32]) -> bool:
    edges = np.concatenate(
        [tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]], axis=0
    )
    edges = np.sort(edges, axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return bool(np.all(counts == 2))
