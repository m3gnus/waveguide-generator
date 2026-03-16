"""
Geometry symmetry detection for automatic model reduction.

Detects whether horn geometry supports:
- Full model
- Half-space model (symmetric about one axis)
- Quarter-space model (symmetric about two axes)

Applies Neumann boundary conditions on symmetry planes for proper BEM formulation.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)
from typing import Dict, List, Tuple, Optional
from enum import Enum


class SymmetryType(Enum):
    """Symmetry types for model reduction"""
    FULL = "full"  # No symmetry
    HALF_X = "half_x"  # Symmetric about X=0 plane (YZ plane)
    HALF_Z = "half_z"  # Symmetric about Z=0 plane (XY plane)
    QUARTER_XZ = "quarter_xz"  # Symmetric about both X=0 and Z=0


class SymmetryPlane(Enum):
    """Symmetry plane definitions"""
    YZ = "yz"  # X = 0 plane
    XY = "xy"  # Z = 0 plane
    XZ = "xz"  # Y = 0 plane


def create_mirror_grid(
    vertices: np.ndarray,
    indices: np.ndarray,
    symmetry_planes: List[SymmetryPlane],
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Build image-source mirror grids for BEM symmetry reduction.

    For each unique combination of the given symmetry planes, reflects the
    mesh and reverses triangle winding so that bempp computes correct
    reflected normals for the DLP and HYP operators.

    Args:
        vertices: Vertex array, shape (3, N), rows are [X, Y, Z].
        indices:  Triangle index array, shape (3, M) — bempp column-major.
        symmetry_planes: Which planes to mirror across.
            SymmetryPlane.YZ  → X = 0  (flip X coordinate)
            SymmetryPlane.XY  → Z = 0  (flip Z coordinate)

    Returns:
        List of (mirrored_vertices, mirrored_indices) tuples, one per image
        contribution (not including the original mesh):
          - Single plane  → 1 tuple  (the mirror across that plane)
          - Two planes    → 3 tuples (X-mirror, Z-mirror, XZ-mirror)

    Notes:
        * Input arrays are never modified; all mirrors are independent copies.
        * Winding is reversed on every mirror by swapping index rows 1 and 2.
        * Only SymmetryPlane.YZ and SymmetryPlane.XY are currently handled;
          SymmetryPlane.XZ is a no-op and will produce no output tuple.
    """
    has_yz = SymmetryPlane.YZ in symmetry_planes   # X = 0 plane
    has_xy = SymmetryPlane.XY in symmetry_planes   # Z = 0 plane

    def _mirror(v: np.ndarray, flip_x: bool, flip_z: bool) -> np.ndarray:
        """Return a copy of v with the requested sign flips."""
        out = v.copy()
        if flip_x:
            out[0, :] = -out[0, :]
        if flip_z:
            out[2, :] = -out[2, :]
        return out

    def _reverse_winding(idx: np.ndarray) -> np.ndarray:
        """Return a copy of idx with rows 1 and 2 swapped (reverses triangle winding)."""
        out = idx.copy()
        out[1, :], out[2, :] = idx[2, :].copy(), idx[1, :].copy()
        return out

    result: List[Tuple[np.ndarray, np.ndarray]] = []

    if has_yz and not has_xy:
        # Half model — single mirror across X = 0
        result.append((_mirror(vertices, flip_x=True, flip_z=False), _reverse_winding(indices)))

    elif has_xy and not has_yz:
        # Half model — single mirror across Z = 0
        result.append((_mirror(vertices, flip_x=False, flip_z=True), _reverse_winding(indices)))

    elif has_yz and has_xy:
        # Quarter model — three image contributions
        result.append((_mirror(vertices, flip_x=True,  flip_z=False), _reverse_winding(indices)))  # X-mirror
        result.append((_mirror(vertices, flip_x=False, flip_z=True),  _reverse_winding(indices)))  # Z-mirror
        result.append((_mirror(vertices, flip_x=True,  flip_z=True),  indices.copy()))             # XZ-mirror: double reflection restores winding

    return result


def symmetry_type_value(symmetry_type: Optional[object]) -> str:
    if isinstance(symmetry_type, SymmetryType):
        return symmetry_type.value
    if symmetry_type is None:
        return SymmetryType.FULL.value
    return str(symmetry_type)


def symmetry_plane_values(symmetry_planes: Optional[List[object]]) -> List[str]:
    values: List[str] = []
    for plane in symmetry_planes or []:
        if isinstance(plane, SymmetryPlane):
            values.append(plane.value)
        else:
            values.append(str(plane))
    return values


def reduction_factor_for_symmetry_type(symmetry_type: Optional[object]) -> float:
    normalized = symmetry_type_value(symmetry_type)
    if normalized == SymmetryType.QUARTER_XZ.value:
        return 4.0
    if normalized in (SymmetryType.HALF_X.value, SymmetryType.HALF_Z.value):
        return 2.0
    return 1.0


def serialize_symmetry_info(symmetry_info: Optional[Dict]) -> Dict:
    info = dict(symmetry_info or {})
    info["symmetry_type"] = symmetry_type_value(info.get("symmetry_type"))
    info["symmetry_planes"] = symmetry_plane_values(info.get("symmetry_planes"))

    if "reduction_factor" in info:
        info["reduction_factor"] = float(info["reduction_factor"])

    for key in (
        "symmetry_face_tag",
        "original_vertices",
        "reduced_vertices",
        "original_triangles",
        "reduced_triangles",
    ):
        if key in info and info[key] is not None:
            info[key] = int(info[key])

    return info


def build_symmetry_policy(
    *,
    requested: bool,
    reason: str,
    detected_symmetry_type: Optional[object] = None,
    detected_symmetry_planes: Optional[List[object]] = None,
    applied: bool = False,
    eligible: bool = False,
    reduction_factor: float = 1.0,
    detected_reduction_factor: Optional[float] = None,
    excitation_centered: Optional[bool] = None,
    throat_center: Optional[np.ndarray] = None,
    error: Optional[str] = None,
) -> Dict[str, object]:
    normalized_type = symmetry_type_value(detected_symmetry_type)
    normalized_planes = symmetry_plane_values(detected_symmetry_planes)
    normalized_detected_reduction = float(
        detected_reduction_factor
        if detected_reduction_factor is not None
        else reduction_factor_for_symmetry_type(normalized_type)
    )

    return {
        "requested": bool(requested),
        "decision": "reduced" if applied else "full_model",
        "reason": str(reason),
        "applied": bool(applied),
        "eligible": bool(eligible),
        "detected_symmetry_type": normalized_type,
        "detected_symmetry_planes": normalized_planes,
        "detected_reduction_factor": normalized_detected_reduction,
        "reduction_factor": float(reduction_factor),
        "excitation_centered": None if excitation_centered is None else bool(excitation_centered),
        "throat_center": None if throat_center is None else [float(value) for value in throat_center],
        "error": None if error is None else str(error),
    }


def _symmetry_from_quadrants(
    quadrants: object,
) -> Tuple[SymmetryType, List[SymmetryPlane]]:
    """Derive symmetry type and planes from the Mesh.Quadrants parameter.

    Quadrant values follow the ATH/frontend convention:
      '1'    → quarter-symmetry (both XZ and YZ planes)
      '12'   → half-symmetry about X=0 (YZ plane)
      '14'   → half-symmetry about Z=0 (XY plane)
      '1234' → full model (no symmetry)
    """
    q = str(quadrants).strip()
    if q == "1":
        return SymmetryType.QUARTER_XZ, [SymmetryPlane.YZ, SymmetryPlane.XY]
    if q == "12":
        return SymmetryType.HALF_X, [SymmetryPlane.YZ]
    if q == "14":
        return SymmetryType.HALF_Z, [SymmetryPlane.XY]
    # '1234' or any unrecognised value → full model
    return SymmetryType.FULL, []


def evaluate_symmetry_policy(
    *,
    vertices: Optional[np.ndarray],
    indices: Optional[np.ndarray],
    surface_tags: Optional[np.ndarray],
    throat_elements: Optional[np.ndarray],
    enable_symmetry: bool,
    tolerance: float = 1e-3,
    quadrants: Optional[object] = None,
) -> Dict[str, object]:
    if not enable_symmetry:
        return {
            "policy": build_symmetry_policy(requested=False, reason="disabled"),
            "symmetry": {"symmetry_type": SymmetryType.FULL.value, "reduction_factor": 1.0},
            "reduced_vertices": vertices,
            "reduced_indices": indices,
            "reduced_surface_tags": surface_tags,
            "symmetry_info": None,
        }

    if vertices is None or indices is None:
        return {
            "policy": build_symmetry_policy(requested=True, reason="missing_original_mesh"),
            "symmetry": {"symmetry_type": SymmetryType.FULL.value, "reduction_factor": 1.0},
            "reduced_vertices": vertices,
            "reduced_indices": indices,
            "reduced_surface_tags": surface_tags,
            "symmetry_info": None,
        }

    # --- Parameter-driven symmetry detection (O(1)) ---
    # When the quadrants parameter is available, use it directly instead of
    # the O(N²) vertex-matching heuristic which always fails on OCC meshes
    # because free meshing does not produce mirror-symmetric vertices.
    if quadrants is not None:
        symmetry_type, symmetry_planes = _symmetry_from_quadrants(quadrants)
        logger.info(
            "[Symmetry] Parameter-driven detection: quadrants=%s → %s, planes=%s",
            quadrants, symmetry_type.value,
            [p.value for p in symmetry_planes],
        )
    else:
        # Legacy fallback: vertex-based detection (kept for non-OCC paths)
        symmetry_type, symmetry_planes = detect_geometric_symmetry(vertices, tolerance=tolerance)
        logger.info(
            "[Symmetry] Vertex-based detection (legacy): %s, planes=%s",
            symmetry_type.value,
            [p.value for p in symmetry_planes],
        )

    base_policy = build_symmetry_policy(
        requested=True,
        reason="no_geometric_symmetry",
        detected_symmetry_type=symmetry_type,
        detected_symmetry_planes=symmetry_planes,
        detected_reduction_factor=reduction_factor_for_symmetry_type(symmetry_type),
    )

    if symmetry_type == SymmetryType.FULL:
        return {
            "policy": base_policy,
            "symmetry": {"symmetry_type": SymmetryType.FULL.value, "reduction_factor": 1.0},
            "reduced_vertices": vertices,
            "reduced_indices": indices,
            "reduced_surface_tags": surface_tags,
            "symmetry_info": None,
        }

    resolved_throat_elements = throat_elements
    if resolved_throat_elements is None:
        resolved_throat_elements = np.array([], dtype=int)

    throat_center = find_throat_center(vertices, resolved_throat_elements, indices)

    # ------------------------------------------------------------------
    # Geometry-first path: if the mesh was already built as a half/quarter
    # model by the OCC builder (quadrants != 1234), the mesh IS the reduced
    # mesh and no clipping is needed.  This follows the tessellation-last
    # principle — geometry is cut BEFORE meshing, not after.
    #
    # IMPORTANT: This check must happen BEFORE excitation symmetry check.
    # For geometry-first half-models, the throat is not at X=0 (it's on the
    # X>=0 side only), so the excitation check would incorrectly reject it.
    # ------------------------------------------------------------------
    _effective_q = int(quadrants) if quadrants is not None else 1234
    if _effective_q != 1234:
        # Mesh was built for a partial geometry — use as-is.
        _reduction = reduction_factor_for_symmetry_type(symmetry_type)
        _sym_info_dict = {
            'symmetry_type': symmetry_type,
            'symmetry_planes': symmetry_planes,
            'symmetry_face_tag': None,
            'reduction_factor': _reduction,
        }
        serialized_info = serialize_symmetry_info(_sym_info_dict)
        policy = build_symmetry_policy(
            requested=True,
            reason="applied",
            detected_symmetry_type=symmetry_type,
            detected_symmetry_planes=symmetry_planes,
            applied=True,
            eligible=True,
            reduction_factor=float(_reduction),
            detected_reduction_factor=float(_reduction),
            excitation_centered=True,
            throat_center=throat_center,
        )
        logger.info(
            "[Symmetry] Geometry-first: mesh already built as %s (quadrants=%s). "
            "No post-tessellation clipping needed.",
            symmetry_type.value, quadrants,
        )
        return {
            "policy": policy,
            "symmetry": serialized_info,
            "reduced_vertices": vertices,
            "reduced_indices": indices,
            "reduced_surface_tags": surface_tags,
            "symmetry_info": serialized_info,
        }

    # ------------------------------------------------------------------
    # For full-model meshes (quadrants=1234), check excitation symmetry.
    # The throat must be centered on the symmetry plane for valid reduction.
    # ------------------------------------------------------------------
    excitation_ok = check_excitation_symmetry(throat_center, symmetry_planes, tolerance=1e-3)
    if not excitation_ok:
        policy = build_symmetry_policy(
            requested=True,
            reason="excitation_off_center",
            detected_symmetry_type=symmetry_type,
            detected_symmetry_planes=symmetry_planes,
            detected_reduction_factor=reduction_factor_for_symmetry_type(symmetry_type),
            excitation_centered=False,
            throat_center=throat_center,
        )
        return {
            "policy": policy,
            "symmetry": {"symmetry_type": SymmetryType.FULL.value, "reduction_factor": 1.0},
            "reduced_vertices": vertices,
            "reduced_indices": indices,
            "reduced_surface_tags": surface_tags,
            "symmetry_info": None,
        }

    # ------------------------------------------------------------------
    # Legacy fallback: clip the mesh at symmetry planes post-tessellation.
    # This path is only reached for full-model meshes (quadrants=1234)
    # where the vertex-matching heuristic found symmetry.  Not recommended
    # (tessellation-last principle — see docs/backlog.md Working Rules).
    # ------------------------------------------------------------------
    reduced_vertices, reduced_indices, reduced_surface_tags, symmetry_info_dict = apply_symmetry_reduction(
        vertices, indices, surface_tags, symmetry_type, symmetry_planes
    )
    serialized_info = serialize_symmetry_info(symmetry_info_dict)
    policy = build_symmetry_policy(
        requested=True,
        reason="applied",
        detected_symmetry_type=symmetry_type,
        detected_symmetry_planes=symmetry_planes,
        applied=True,
        eligible=True,
        reduction_factor=float(serialized_info["reduction_factor"]),
        detected_reduction_factor=float(serialized_info["reduction_factor"]),
        excitation_centered=True,
        throat_center=throat_center,
    )
    return {
        "policy": policy,
        "symmetry": serialized_info,
        "reduced_vertices": reduced_vertices,
        "reduced_indices": reduced_indices,
        "reduced_surface_tags": reduced_surface_tags,
        "symmetry_info": serialized_info,
    }


def detect_geometric_symmetry(
    vertices: np.ndarray,
    tolerance: float = 1e-3
) -> Tuple[SymmetryType, List[SymmetryPlane]]:
    """
    Detect geometric symmetry in mesh.

    Args:
        vertices: Vertex array (3, N) with [X, Y, Z] coordinates
        tolerance: Relative tolerance for symmetry check (default: 0.1% of max dimension)

    Returns:
        (symmetry_type, symmetry_planes)
    """
    if vertices.shape[0] != 3:
        raise ValueError(f"Vertices must have shape (3, N), got {vertices.shape}")

    # Calculate bounding box for tolerance scaling
    mins = np.min(vertices, axis=1)
    maxs = np.max(vertices, axis=1)
    extents = maxs - mins
    max_extent = np.max(extents)

    # Absolute tolerance based on geometry size
    abs_tol = tolerance * max_extent

    # Check each axis for symmetry
    has_x_symmetry = _check_plane_symmetry(vertices, axis=0, tolerance=abs_tol)
    has_z_symmetry = _check_plane_symmetry(vertices, axis=2, tolerance=abs_tol)

    symmetry_planes = []

    if has_x_symmetry:
        symmetry_planes.append(SymmetryPlane.YZ)
    if has_z_symmetry:
        symmetry_planes.append(SymmetryPlane.XY)

    # Determine symmetry type
    if has_x_symmetry and has_z_symmetry:
        return SymmetryType.QUARTER_XZ, symmetry_planes
    elif has_x_symmetry:
        return SymmetryType.HALF_X, symmetry_planes
    elif has_z_symmetry:
        return SymmetryType.HALF_Z, symmetry_planes
    else:
        return SymmetryType.FULL, symmetry_planes


def _check_plane_symmetry(
    vertices: np.ndarray,
    axis: int,
    tolerance: float
) -> bool:
    """
    Check if geometry is symmetric about axis=0 plane.

    Args:
        vertices: Vertex array (3, N)
        axis: 0=X, 1=Y, 2=Z
        tolerance: Absolute tolerance in same units as vertices

    Returns:
        True if symmetric about the plane
    """
    # Get coordinates along the symmetry axis
    coords = vertices[axis, :]

    # Check if geometry crosses the plane (has both positive and negative values)
    has_positive = np.any(coords > tolerance)
    has_negative = np.any(coords < -tolerance)

    if not (has_positive and has_negative):
        # Geometry is entirely on one side - not symmetric, but may be on the plane
        # Check if all points are near the plane (edge case: 2D geometry)
        if np.all(np.abs(coords) < tolerance):
            return True  # Degenerate case: all points on plane
        return False

    # For each vertex on positive side, check if mirror exists on negative side
    positive_mask = coords > tolerance
    negative_mask = coords < -tolerance

    positive_verts = vertices[:, positive_mask]
    negative_verts = vertices[:, negative_mask]

    if positive_verts.shape[1] == 0 or negative_verts.shape[1] == 0:
        return False

    # For each positive vertex, try to find its mirror
    for i in range(positive_verts.shape[1]):
        vert = positive_verts[:, i].copy()
        # Mirror it across the plane
        vert[axis] = -vert[axis]

        # Find closest negative vertex
        distances = np.linalg.norm(negative_verts - vert[:, np.newaxis], axis=0)
        min_dist = np.min(distances)

        if min_dist > tolerance:
            return False

    # Check the reverse direction
    for i in range(negative_verts.shape[1]):
        vert = negative_verts[:, i].copy()
        vert[axis] = -vert[axis]

        distances = np.linalg.norm(positive_verts - vert[:, np.newaxis], axis=0)
        min_dist = np.min(distances)

        if min_dist > tolerance:
            return False

    return True


def check_excitation_symmetry(
    throat_center: np.ndarray,
    symmetry_planes: List[SymmetryPlane],
    tolerance: float = 1e-6
) -> bool:
    """
    Check if excitation (throat) is centered on symmetry planes.

    Args:
        throat_center: [X, Y, Z] coordinates of throat center
        symmetry_planes: List of detected symmetry planes
        tolerance: Absolute tolerance for center position

    Returns:
        True if throat is properly centered for all symmetry planes
    """
    for plane in symmetry_planes:
        if plane == SymmetryPlane.YZ:
            # X = 0 plane: throat must be centered at X=0
            if abs(throat_center[0]) > tolerance:
                return False
        elif plane == SymmetryPlane.XY:
            # Z = 0 plane: throat must be centered at Z=0
            if abs(throat_center[2]) > tolerance:
                return False
        elif plane == SymmetryPlane.XZ:
            # Y = 0 plane: throat must be centered at Y=0
            if abs(throat_center[1]) > tolerance:
                return False

    return True


def find_throat_center(
    vertices: np.ndarray,
    throat_elements: np.ndarray,
    indices: np.ndarray
) -> np.ndarray:
    """
    Calculate centroid of throat surface.

    Args:
        vertices: Vertex array (3, N)
        throat_elements: Indices of triangles belonging to throat
        indices: Triangle indices (3, M) or flat array

    Returns:
        [X, Y, Z] coordinates of throat center
    """
    if len(throat_elements) == 0:
        # Fallback: use minimum Y coordinate (throat is typically at min Y)
        min_y = np.min(vertices[1, :])
        throat_mask = np.abs(vertices[1, :] - min_y) < 1.0
        throat_verts = vertices[:, throat_mask]
        if throat_verts.shape[1] > 0:
            return np.mean(throat_verts, axis=1)
        else:
            return np.array([0.0, min_y, 0.0])

    # Reshape indices if flat
    if indices.ndim == 1:
        indices = indices.reshape(-1, 3)
    elif indices.shape[0] == 3:
        indices = indices.T  # Convert to (M, 3)

    # Collect all throat vertices
    throat_verts = []
    for elem_idx in throat_elements:
        if elem_idx < indices.shape[0]:
            tri = indices[elem_idx, :]
            for v_idx in tri:
                if v_idx < vertices.shape[1]:
                    throat_verts.append(vertices[:, v_idx])

    if len(throat_verts) == 0:
        # Fallback
        return np.array([0.0, np.min(vertices[1, :]), 0.0])

    throat_verts = np.array(throat_verts).T  # Shape (3, K)
    return np.mean(throat_verts, axis=1)


def identify_symmetry_faces(
    vertices: np.ndarray,
    indices: np.ndarray,
    symmetry_planes: List[SymmetryPlane],
    tolerance: float = 1e-3
) -> Dict[SymmetryPlane, np.ndarray]:
    """
    Identify triangular faces that lie on symmetry planes.

    These faces need Neumann boundary conditions (rigid symmetry plane).

    Args:
        vertices: Vertex array (3, N)
        indices: Triangle indices (3, M) or flat array
        symmetry_planes: List of symmetry planes to check
        tolerance: Absolute tolerance for plane proximity

    Returns:
        Dictionary mapping SymmetryPlane -> array of triangle indices
    """
    # Reshape indices if needed
    if indices.ndim == 1:
        indices = indices.reshape(-1, 3)
    elif indices.shape[0] == 3:
        indices = indices.T

    num_triangles = indices.shape[0]

    symmetry_faces = {}

    for plane in symmetry_planes:
        # Determine which axis is perpendicular to the plane
        if plane == SymmetryPlane.YZ:
            axis = 0  # X = 0
        elif plane == SymmetryPlane.XY:
            axis = 2  # Z = 0
        elif plane == SymmetryPlane.XZ:
            axis = 1  # Y = 0
        else:
            continue

        # Find triangles where all vertices are on the plane
        on_plane_triangles = []

        for tri_idx in range(num_triangles):
            v_indices = indices[tri_idx, :]

            # Check if all vertices are valid
            if np.any(v_indices >= vertices.shape[1]):
                continue

            # Get vertex coordinates along the symmetry axis
            v_coords = vertices[axis, v_indices]

            # All vertices must be near the plane
            if np.all(np.abs(v_coords) < tolerance):
                on_plane_triangles.append(tri_idx)

        symmetry_faces[plane] = np.array(on_plane_triangles, dtype=int)

    return symmetry_faces


def clip_mesh_at_plane(
    vertices: np.ndarray,
    indices: np.ndarray,
    surface_tags: Optional[np.ndarray],
    axis: int,
    keep_positive: bool = True,
    tolerance: float = 1e-6,
) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Clip a triangle mesh against a plane, splitting straddling triangles.

    Args:
        vertices: Vertex array, shape (3, N).
        indices: Triangle index array, shape (3, M) or (M, 3).
        surface_tags: Per-triangle tags, shape (M,), or None.
        axis: Perpendicular axis of the clipping plane (0=X, 1=Y, 2=Z).
        keep_positive: If True, keep the side where coord >= 0.
        tolerance: Vertices within this distance of the plane are considered on-plane.

    Returns:
        (clipped_vertices, clipped_indices, clipped_tags) where
        clipped_vertices is (3, N'), clipped_indices is (3, M'),
        clipped_tags is (M',) or None.
    """
    # Normalise indices to (M, 3) for processing
    if indices.ndim == 2 and indices.shape[0] == 3 and indices.shape[1] != 3:
        idx = indices.T.copy()
    elif indices.ndim == 2 and indices.shape[1] == 3:
        idx = indices.copy()
    elif indices.ndim == 2 and indices.shape[0] == 3 and indices.shape[1] == 3:
        # Ambiguous 3x3 — treat as bempp column-major (3, M)
        idx = indices.T.copy()
    else:
        idx = indices.copy()

    num_tris = idx.shape[0]

    # Build a mutable vertex list (start from existing vertices)
    verts = vertices.copy()  # (3, N)
    new_verts_list: List[np.ndarray] = []  # extra vertices to append
    next_vert_id = verts.shape[1]

    # Classify all vertices: +1 = positive/kept, -1 = negative/discarded, 0 = on-plane (kept)
    coords = verts[axis, :]
    classification = np.zeros(verts.shape[1], dtype=int)
    classification[coords > tolerance] = 1
    classification[coords < -tolerance] = -1
    # on-plane (|coord| <= tolerance) stays 0

    if not keep_positive:
        classification = -classification

    # For kept-side logic: +1 and 0 are kept, -1 is discarded
    # kept = classification >= 0

    def _get_or_create_intersection(p1_idx: int, p2_idx: int) -> int:
        """Compute intersection of edge p1->p2 with the plane, return vertex index."""
        nonlocal next_vert_id
        p1 = verts[:, p1_idx] if p1_idx < verts.shape[1] else new_verts_list[p1_idx - verts.shape[1]]
        p2 = verts[:, p2_idx] if p2_idx < verts.shape[1] else new_verts_list[p2_idx - verts.shape[1]]
        # t such that p1[axis] + t*(p2[axis] - p1[axis]) = 0
        denom = p2[axis] - p1[axis]
        if abs(denom) < 1e-15:
            # Edge is parallel to the plane — shouldn't happen if one is positive and one negative
            t = 0.5
        else:
            t = -p1[axis] / denom
        intersection = p1 + t * (p2 - p1)
        intersection[axis] = 0.0  # Snap to plane exactly
        new_verts_list.append(intersection)
        vid = next_vert_id
        next_vert_id += 1
        return vid

    def _classify(v_idx: int) -> int:
        if v_idx < len(classification):
            return classification[v_idx]
        # New vertex — it's on the plane
        return 0

    out_tris: List[List[int]] = []
    out_tags: List[int] = []

    for ti in range(num_tris):
        v0, v1, v2 = int(idx[ti, 0]), int(idx[ti, 1]), int(idx[ti, 2])
        c0, c1, c2 = _classify(v0), _classify(v1), _classify(v2)

        # Kept = classification >= 0 (positive or on-plane)
        k0, k1, k2 = (c0 >= 0), (c1 >= 0), (c2 >= 0)
        kept_count = int(k0) + int(k1) + int(k2)

        tag = surface_tags[ti] if surface_tags is not None and ti < len(surface_tags) else 1

        if kept_count == 3:
            # All kept
            out_tris.append([v0, v1, v2])
            out_tags.append(tag)
        elif kept_count == 0:
            # All discarded
            continue
        elif kept_count == 1:
            # Case A: 1 vertex kept, 2 discarded
            # Find the kept vertex
            if k0:
                a, b, c = v0, v1, v2
            elif k1:
                a, b, c = v1, v2, v0
            else:
                a, b, c = v2, v0, v1

            ca = _classify(a)
            cb = _classify(b)
            cc = _classify(c)

            # Intersect edges a->b and a->c (a is kept, b and c are discarded)
            i_ab = _get_or_create_intersection(a, b) if cb < 0 else b
            i_ac = _get_or_create_intersection(a, c) if cc < 0 else c

            out_tris.append([a, i_ab, i_ac])
            out_tags.append(tag)
        else:
            # Case B: 2 vertices kept, 1 discarded
            # Find the discarded vertex
            if not k0:
                c_disc, a_kept, b_kept = v0, v1, v2
            elif not k1:
                c_disc, a_kept, b_kept = v1, v2, v0
            else:
                c_disc, a_kept, b_kept = v2, v0, v1

            cc = _classify(c_disc)
            ca = _classify(a_kept)
            cb = _classify(b_kept)

            # Intersect edges a->c and b->c (c is discarded, a and b are kept)
            i_ac = _get_or_create_intersection(a_kept, c_disc) if cc < 0 else c_disc
            i_bc = _get_or_create_intersection(b_kept, c_disc) if cc < 0 else c_disc

            out_tris.append([a_kept, b_kept, i_bc])
            out_tags.append(tag)
            out_tris.append([a_kept, i_bc, i_ac])
            out_tags.append(tag)

    if len(out_tris) == 0:
        raise ValueError("clip_mesh_at_plane: clipping resulted in empty mesh")

    # Build final vertex array
    if new_verts_list:
        extra = np.array(new_verts_list).T  # (3, K)
        clipped_verts = np.hstack([verts, extra])
    else:
        clipped_verts = verts

    clipped_indices = np.array(out_tris, dtype=int).T  # (3, M')
    clipped_tags_arr = np.array(out_tags, dtype=int) if surface_tags is not None else None

    # --- Remove degenerate (zero-area) triangles produced by clipping ---
    v0 = clipped_verts[:, clipped_indices[0]]  # (3, M')
    v1 = clipped_verts[:, clipped_indices[1]]
    v2 = clipped_verts[:, clipped_indices[2]]
    cross = np.cross((v1 - v0).T, (v2 - v0).T).T  # (3, M')
    areas = 0.5 * np.sqrt(np.sum(cross ** 2, axis=0))
    min_area = 1e-14  # ~0.1 nm² — filters only truly degenerate triangles
    valid = areas > min_area
    if not np.all(valid):
        clipped_indices = clipped_indices[:, valid]
        if clipped_tags_arr is not None:
            clipped_tags_arr = clipped_tags_arr[valid]
        if clipped_indices.shape[1] == 0:
            raise ValueError("clip_mesh_at_plane: all triangles degenerate after clipping")

    return clipped_verts, clipped_indices, clipped_tags_arr


def apply_symmetry_reduction(
    vertices: np.ndarray,
    indices: np.ndarray,
    surface_tags: Optional[np.ndarray],
    symmetry_type: SymmetryType,
    symmetry_planes: List[SymmetryPlane]
) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], Dict]:
    """
    Apply symmetry reduction to mesh.

    Keeps only the positive quadrant(s) and tags symmetry plane faces.

    Args:
        vertices: Original vertex array (3, N)
        indices: Original triangle indices
        surface_tags: Original surface tags (1=throat, 2=wall, 3=mouth)
        symmetry_type: Type of symmetry detected
        symmetry_planes: List of symmetry planes

    Returns:
        (reduced_vertices, reduced_indices, reduced_tags, reduction_info)

        reduction_info contains:
            - 'symmetry_type': SymmetryType
            - 'symmetry_planes': List[SymmetryPlane]
            - 'symmetry_face_tag': int (tag for symmetry plane faces)
            - 'reduction_factor': float (e.g., 2.0 for half, 4.0 for quarter)
    """
    if symmetry_type == SymmetryType.FULL:
        # No reduction
        return vertices, indices, surface_tags, {
            'symmetry_type': symmetry_type,
            'symmetry_planes': [],
            'symmetry_face_tag': None,
            'reduction_factor': 1.0
        }

    # Reshape indices to (3, M) bempp column-major if needed
    work_indices = indices
    if work_indices.ndim == 1:
        work_indices = work_indices.reshape(-1, 3).T
    elif work_indices.shape[0] != 3 and work_indices.shape[1] == 3:
        work_indices = work_indices.T

    # Tag for symmetry plane faces (canonical contract: 1=wall, 2=source, 3=secondary, 4=interface/symmetry)
    SYMMETRY_TAG = 4

    # --- Clip mesh at each symmetry plane (splits straddling triangles) ---
    clipped_verts = vertices
    clipped_indices = work_indices
    clipped_tags = surface_tags

    if SymmetryPlane.YZ in symmetry_planes:
        # Clip at X=0 plane, keep X >= 0
        clipped_verts, clipped_indices, clipped_tags = clip_mesh_at_plane(
            clipped_verts, clipped_indices, clipped_tags, axis=0, keep_positive=True
        )

    if SymmetryPlane.XY in symmetry_planes:
        # Clip at Z=0 plane, keep Z >= 0
        clipped_verts, clipped_indices, clipped_tags = clip_mesh_at_plane(
            clipped_verts, clipped_indices, clipped_tags, axis=2, keep_positive=True
        )

    # --- Compact vertices (remove unused vertices after clipping) ---
    # clipped_indices is (3, M') from clip_mesh_at_plane
    used_verts = np.unique(clipped_indices.ravel())
    old_to_new = np.full(clipped_verts.shape[1], -1, dtype=int)
    for new_idx, old_idx in enumerate(used_verts):
        old_to_new[old_idx] = new_idx

    reduced_vertices = clipped_verts[:, used_verts]
    reduced_indices = old_to_new[clipped_indices]  # (3, M')

    # --- Identify symmetry plane faces on the clipped mesh and apply tag ---
    symmetry_face_dict = identify_symmetry_faces(
        reduced_vertices, reduced_indices, symmetry_planes, tolerance=1e-3
    )
    symmetry_face_set = set()
    for plane, faces in symmetry_face_dict.items():
        symmetry_face_set.update(faces)

    num_clipped_tris = reduced_indices.shape[1]
    # Build reduced tags: start from clipped_tags, then override symmetry faces
    reduced_tags_list = []
    for ti in range(num_clipped_tris):
        if ti in symmetry_face_set:
            reduced_tags_list.append(SYMMETRY_TAG)
        elif clipped_tags is not None and ti < len(clipped_tags):
            reduced_tags_list.append(clipped_tags[ti])
        else:
            reduced_tags_list.append(1)  # Default to wall

    if num_clipped_tris == 0:
        raise ValueError("Symmetry reduction resulted in empty mesh")

    reduced_surface_tags = np.array(reduced_tags_list, dtype=int) if reduced_tags_list else None

    # Calculate reduction factor
    if symmetry_type == SymmetryType.QUARTER_XZ:
        reduction_factor = 4.0
    elif symmetry_type in (SymmetryType.HALF_X, SymmetryType.HALF_Z):
        reduction_factor = 2.0
    else:
        reduction_factor = 1.0

    reduction_info = {
        'symmetry_type': symmetry_type,
        'symmetry_planes': symmetry_planes,
        'symmetry_face_tag': SYMMETRY_TAG,
        'reduction_factor': reduction_factor,
        'original_vertices': vertices.shape[1],
        'reduced_vertices': reduced_vertices.shape[1],
        'original_triangles': work_indices.shape[1],
        'reduced_triangles': reduced_indices.shape[1]
    }

    return reduced_vertices, reduced_indices, reduced_surface_tags, reduction_info


def validate_symmetry_reduction(
    reduction_info: Dict,
    verbose: bool = True
) -> bool:
    """
    Validate that symmetry reduction was successful.

    Args:
        reduction_info: Info dict from apply_symmetry_reduction
        verbose: Print validation messages

    Returns:
        True if reduction is valid
    """
    if reduction_info['reduction_factor'] <= 1.0:
        if verbose:
            logger.info("[Symmetry] No reduction applied (full model)")
        return True

    expected_reduction = reduction_info['reduction_factor']
    actual_vert_reduction = reduction_info['original_vertices'] / reduction_info['reduced_vertices']
    actual_tri_reduction = reduction_info['original_triangles'] / reduction_info['reduced_triangles']

    # Allow some tolerance (mesh boundaries may not reduce perfectly)
    if actual_tri_reduction < expected_reduction * 0.7:
        if verbose:
            logger.warning(
                "[Symmetry] Expected %.1fx reduction, got %.2fx", expected_reduction, actual_tri_reduction
            )
        return False

    if verbose:
        logger.info("[Symmetry] Reduction validated: %.2fx triangle reduction", actual_tri_reduction)
        logger.info(
            "[Symmetry] Vertices: %d -> %d",
            reduction_info['original_vertices'], reduction_info['reduced_vertices'],
        )
        logger.info(
            "[Symmetry] Triangles: %d -> %d",
            reduction_info['original_triangles'], reduction_info['reduced_triangles'],
        )

    return True
