import logging
import numpy as np
from typing import Dict

logger = logging.getLogger(__name__)


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-15:
        return vec
    return vec / norm


def _principal_axis(vertices: np.ndarray, center: np.ndarray) -> np.ndarray:
    centered = vertices - center[:, None]
    if centered.shape[1] == 0:
        return np.array([0.0, 1.0, 0.0], dtype=np.float64)
    cov = centered @ centered.T
    evals, evecs = np.linalg.eigh(cov)
    axis = evecs[:, int(np.argmax(evals))]
    axis = _normalize(np.asarray(axis, dtype=np.float64))
    if float(np.linalg.norm(axis)) <= 1e-12:
        return np.array([0.0, 1.0, 0.0], dtype=np.float64)
    return axis


def _source_axis_and_center(
    vertices: np.ndarray,
    elements: np.ndarray,
    domain_indices: np.ndarray,
    fallback_center: np.ndarray,
):
    if elements.ndim != 2 or elements.shape[0] != 3 or elements.shape[1] == 0:
        return None, fallback_center

    source_elem_ids = np.flatnonzero(domain_indices == 2)
    if source_elem_ids.size == 0:
        return None, fallback_center
    source_elem_ids = source_elem_ids[source_elem_ids < elements.shape[1]]
    if source_elem_ids.size == 0:
        return None, fallback_center

    source_elements = elements[:, source_elem_ids]
    vertex_count = vertices.shape[1]
    valid_col_mask = np.all((source_elements >= 0) & (source_elements < vertex_count), axis=0)
    if not np.any(valid_col_mask):
        return None, fallback_center
    source_elements = source_elements[:, valid_col_mask]

    p0 = vertices[:, source_elements[0]]
    p1 = vertices[:, source_elements[1]]
    p2 = vertices[:, source_elements[2]]

    normals = np.cross((p1 - p0).T, (p2 - p0).T)
    area2 = np.linalg.norm(normals, axis=1)
    valid_area_mask = area2 > 1e-15
    if not np.any(valid_area_mask):
        return None, fallback_center

    normals = normals[valid_area_mask]
    area2 = area2[valid_area_mask]
    centroids = ((p0 + p1 + p2) / 3.0).T[valid_area_mask]
    total_area = float(np.sum(area2))
    source_center = np.sum(centroids * area2[:, None], axis=0) / total_area

    # Align normals into one hemisphere so mixed winding does not cancel the axis.
    ref = normals[0]
    signs = np.sign(normals @ ref)
    signs[signs == 0] = 1.0
    normals_sum = np.sum(normals * signs[:, None], axis=0)
    axis_norm = float(np.linalg.norm(normals_sum))
    if axis_norm <= 1e-12:
        return None, source_center

    return normals_sum / axis_norm, source_center


def infer_observation_frame(
    grid,
    observation_origin: str = "mouth",
    symmetry_plane: str | None = None,
) -> Dict[str, np.ndarray]:
    """
    Infer a robust radiation frame from the mesh.

    Observation distance is measured from the mouth plane center by default (IEC 60268-5).
    Using the throat as origin introduces a ~6% error at 2m (throat-to-mouth offset ~120mm)
    and ~20% error at near-field (0.5m). The mouth center is the physically correct origin
    because measurement distance conventions (e.g. IEC 60268-5) reference the radiating aperture.

    Args:
        grid: BEM grid object with .vertices, .elements, and .domain_indices attributes.
        observation_origin: "mouth" (default, IEC 60268-5) — measures from the radiating
            aperture; "throat" — measures from the driver/source disc centroid.
        symmetry_plane: Optional symmetry plane identifier ("yz", "xy", or None).
            When "yz" is active (half-model in X>=0), the observation origin's X-coordinate
            is projected to X=0 for correct image-source physics and comparison with full models.

    Returns:
        {
            "axis": forward unit vector (throat -> mouth),
            "origin_center": measurement origin (mouth or throat depending on observation_origin),
            "mouth_center": mouth plane center (always computed, for diagnostic use),
            "source_center": throat disc centroid (always computed, for diagnostic use),
            "u": transverse unit vector (horizontal reference),
            "v": transverse unit vector orthogonal to u (vertical reference)
        }
    """
    vertices = np.asarray(grid.vertices, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[0] != 3 or vertices.shape[1] == 0:
        return {
            "axis": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            "origin_center": np.array([0.0, 0.0, 0.0], dtype=np.float64),  # fallback origin
            "mouth_center": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "source_center": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "u": np.array([1.0, 0.0, 0.0], dtype=np.float64),
            "v": np.array([0.0, 0.0, 1.0], dtype=np.float64),
        }

    source_center = np.mean(vertices, axis=1)
    axis_candidate = None

    try:
        elements = np.asarray(grid.elements, dtype=np.int64)
        domain_indices = np.asarray(grid.domain_indices, dtype=np.int64).reshape(-1)
        axis_candidate, source_center = _source_axis_and_center(
            vertices, elements, domain_indices, source_center
        )
    except Exception:
        axis_candidate = None

    if axis_candidate is None or float(np.linalg.norm(axis_candidate)) <= 1e-12:
        axis_candidate = _principal_axis(vertices, source_center)
    else:
        axis_candidate = _normalize(np.asarray(axis_candidate, dtype=np.float64))

    rel = vertices - source_center[:, None]
    projections = axis_candidate @ rel
    max_proj = float(np.max(projections))
    min_proj = float(np.min(projections))

    if max_proj < -min_proj:
        axis_candidate = -axis_candidate
        projections = -projections
        max_proj, min_proj = -min_proj, -max_proj

    tol = max(1e-6, 0.02 * max(abs(max_proj - min_proj), 1e-6))
    mouth_mask = projections >= (max_proj - tol)
    if np.any(mouth_mask):
        mouth_center = np.mean(vertices[:, mouth_mask], axis=1)
    else:
        mouth_center = source_center + axis_candidate * max_proj

    x_ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    y_ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    u = x_ref - float(np.dot(x_ref, axis_candidate)) * axis_candidate
    if float(np.linalg.norm(u)) <= 1e-12:
        u = y_ref - float(np.dot(y_ref, axis_candidate)) * axis_candidate
    u = _normalize(u)
    if float(np.linalg.norm(u)) <= 1e-12:
        u = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    v = _normalize(np.cross(u, axis_candidate))
    if float(np.linalg.norm(v)) <= 1e-12:
        v = np.array([0.0, 0.0, 1.0], dtype=np.float64)

    # Select measurement origin based on observation_origin parameter.
    # "mouth" (default, IEC 60268-5): distance measured from the radiating aperture.
    # "throat": distance measured from the driver/source disc centroid.
    use_throat = str(observation_origin).strip().lower() == "throat"
    origin_center = source_center.copy() if use_throat else mouth_center.copy()

    # Project origin to symmetry plane for half/quarter models.
    # For YZ symmetry (X=0 plane), the half-mesh has vertices at X>=0, but the
    # effective acoustic center for the full model is at X=0 (symmetry plane).
    if symmetry_plane is not None:
        plane = str(symmetry_plane).strip().lower()
        if plane == "yz":
            origin_center[0] = 0.0
        elif plane == "xy":
            origin_center[2] = 0.0

    logger.info(
        "[observation] frame: axis=[%.4f, %.4f, %.4f], "
        "origin=[%.4f, %.4f, %.4f], "
        "source_center=[%.4f, %.4f, %.4f], "
        "mouth_center=[%.4f, %.4f, %.4f], "
        "observation_origin=%s",
        *axis_candidate, *origin_center, *source_center, *mouth_center,
        observation_origin,
    )
    return {
        "axis": axis_candidate,
        "origin_center": origin_center,
        "mouth_center": mouth_center,
        "source_center": source_center,
        "u": u,
        "v": v,
    }


def point_from_polar(
    origin_center: np.ndarray,
    axis: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    radius_m: float,
    theta_rad: float,
    phi_rad: float,
) -> np.ndarray:
    direction = (
        np.cos(theta_rad) * axis
        + np.sin(theta_rad) * (np.cos(phi_rad) * u + np.sin(phi_rad) * v)
    )
    return origin_center + float(radius_m) * direction


def observation_axis_bounds(
    grid,
    frame: Dict[str, np.ndarray],
) -> Dict[str, float]:
    vertices = np.asarray(grid.vertices, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[0] != 3 or vertices.shape[1] == 0:
        return {"min_proj": 0.0, "max_proj": 0.0, "extent": 0.0}

    origin_center = np.asarray(frame.get("origin_center", np.mean(vertices, axis=1)), dtype=np.float64)
    axis = _normalize(np.asarray(frame.get("axis", np.array([0.0, 1.0, 0.0])), dtype=np.float64))
    if float(np.linalg.norm(axis)) <= 1e-12:
        axis = np.array([0.0, 1.0, 0.0], dtype=np.float64)

    projections = axis @ (vertices - origin_center[:, None])
    min_proj = float(np.min(projections))
    max_proj = float(np.max(projections))
    return {
        "min_proj": min_proj,
        "max_proj": max_proj,
        "extent": float(max_proj - min_proj),
    }


def resolve_safe_observation_distance(
    grid,
    requested_distance_m: float,
    frame: Dict[str, np.ndarray],
    minimum_clearance_m: float = 0.05,
    relative_clearance: float = 0.05,
) -> Dict[str, float | bool]:
    requested = float(requested_distance_m)
    bounds = observation_axis_bounds(grid, frame)
    extent = max(float(bounds["extent"]), 0.0)
    clearance = max(float(minimum_clearance_m), extent * float(relative_clearance))
    min_safe_distance = float(bounds["max_proj"]) + clearance
    effective_distance = max(requested, min_safe_distance)
    return {
        "requested_distance_m": requested,
        "effective_distance_m": effective_distance,
        "min_safe_distance_m": min_safe_distance,
        "clearance_m": clearance,
        "max_projection_m": float(bounds["max_proj"]),
        "adjusted": effective_distance > requested + 1e-12,
    }
