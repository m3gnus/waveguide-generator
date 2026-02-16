"""
Geometry symmetry detection for automatic model reduction.

Detects whether horn geometry supports:
- Full model
- Half-space model (symmetric about one axis)
- Quarter-space model (symmetric about two axes)

Applies Neumann boundary conditions on symmetry planes for proper BEM formulation.
"""

import numpy as np
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

    # Reshape indices if needed
    if indices.ndim == 1:
        indices = indices.reshape(-1, 3)
    elif indices.shape[0] == 3:
        indices = indices.T

    # Tag for symmetry plane faces (canonical contract: 1=wall, 2=source, 3=secondary, 4=interface/symmetry)
    SYMMETRY_TAG = 4

    # Determine which vertices to keep
    keep_mask = np.ones(vertices.shape[1], dtype=bool)

    if SymmetryPlane.YZ in symmetry_planes:
        # Keep X >= 0
        keep_mask &= (vertices[0, :] >= -1e-6)

    if SymmetryPlane.XY in symmetry_planes:
        # Keep Z >= 0
        keep_mask &= (vertices[2, :] >= -1e-6)

    # Build vertex mapping
    old_to_new = np.full(vertices.shape[1], -1, dtype=int)
    new_idx = 0
    for old_idx in range(vertices.shape[1]):
        if keep_mask[old_idx]:
            old_to_new[old_idx] = new_idx
            new_idx += 1

    # Extract kept vertices
    reduced_vertices = vertices[:, keep_mask]

    # Process triangles
    reduced_triangles = []
    reduced_tags = []

    # Identify symmetry plane faces
    symmetry_face_dict = identify_symmetry_faces(
        vertices, indices, symmetry_planes, tolerance=1e-3
    )
    symmetry_face_set = set()
    for plane, faces in symmetry_face_dict.items():
        symmetry_face_set.update(faces)

    for tri_idx in range(indices.shape[0]):
        tri = indices[tri_idx, :]

        # Check if all vertices are kept
        if np.all(keep_mask[tri]):
            # Remap vertex indices
            new_tri = [old_to_new[v] for v in tri]
            reduced_triangles.append(new_tri)

            # Determine tag
            if tri_idx in symmetry_face_set:
                reduced_tags.append(SYMMETRY_TAG)
            elif surface_tags is not None and tri_idx < len(surface_tags):
                reduced_tags.append(surface_tags[tri_idx])
            else:
                reduced_tags.append(1)  # Default to wall

    if len(reduced_triangles) == 0:
        raise ValueError("Symmetry reduction resulted in empty mesh")

    reduced_indices = np.array(reduced_triangles, dtype=int)
    reduced_surface_tags = np.array(reduced_tags, dtype=int) if reduced_tags else None

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
        'original_triangles': indices.shape[0],
        'reduced_triangles': reduced_indices.shape[0]
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
            print("[Symmetry] No reduction applied (full model)")
        return True

    expected_reduction = reduction_info['reduction_factor']
    actual_vert_reduction = reduction_info['original_vertices'] / reduction_info['reduced_vertices']
    actual_tri_reduction = reduction_info['original_triangles'] / reduction_info['reduced_triangles']

    # Allow some tolerance (mesh boundaries may not reduce perfectly)
    if actual_tri_reduction < expected_reduction * 0.7:
        if verbose:
            print(f"[Symmetry] Warning: Expected {expected_reduction}× reduction, got {actual_tri_reduction:.2f}×")
        return False

    if verbose:
        print(f"[Symmetry] Reduction validated: {actual_tri_reduction:.2f}× triangle reduction")
        print(f"[Symmetry] Vertices: {reduction_info['original_vertices']} → {reduction_info['reduced_vertices']}")
        print(f"[Symmetry] Triangles: {reduction_info['original_triangles']} → {reduction_info['reduced_triangles']}")

    return True
