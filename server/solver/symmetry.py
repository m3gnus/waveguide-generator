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
    # Post-tessellation clipping removed (tessellation-last principle).
    # For full-model meshes (quadrants=1234) where vertex-matching found
    # symmetry, we cannot safely clip the tessellated mesh because OCC
    # free-meshing produces asymmetric vertices (measured: 14.8 dB BEM error).
    # Return the full mesh without reduction.
    # ------------------------------------------------------------------
    logger.warning(
        "[Symmetry] Full-model mesh with detected symmetry cannot be reduced "
        "(tessellation-last principle). Use geometry-first approach instead."
    )
    policy = build_symmetry_policy(
        requested=True,
        reason="post_tessellation_clipping_disabled",
        detected_symmetry_type=symmetry_type,
        detected_symmetry_planes=symmetry_planes,
        detected_reduction_factor=reduction_factor_for_symmetry_type(symmetry_type),
        excitation_centered=True,
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


def validate_symmetry_reduction(
    reduction_info: Dict,
    verbose: bool = True
) -> bool:
    """
    Validate that symmetry reduction was successful.

    Args:
        reduction_info: Info dict with reduction metadata (from geometry-first path)
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
