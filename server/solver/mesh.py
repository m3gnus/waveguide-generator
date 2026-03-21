import logging

import numpy as np
from typing import Any, Dict, List, Optional, Tuple

from .deps import bempp_api
from .mesh_cleaner import extract_physical_tags, load_and_clean_msh as _clean_load_msh

logger = logging.getLogger(__name__)


def _resolve_unit_scale_to_meter(
    vertices_array: np.ndarray, mesh_metadata: Optional[Dict[str, Any]]
) -> Tuple[float, str, List[str], float]:
    metadata = mesh_metadata or {}
    warnings: List[str] = []

    raw_scale = metadata.get("unitScaleToMeter")
    if raw_scale is not None:
        try:
            parsed_scale = float(raw_scale)
        except (TypeError, ValueError):
            parsed_scale = float("nan")
        if np.isfinite(parsed_scale) and parsed_scale > 0:
            return parsed_scale, "metadata.unitScaleToMeter", warnings, float(
                np.max(np.ptp(vertices_array, axis=1))
            )
        warnings.append(
            "mesh.metadata.unitScaleToMeter was invalid; falling back to units/heuristic detection."
        )

    raw_units = str(metadata.get("units", "")).strip().lower()
    if raw_units in {"m", "meter", "meters"}:
        return 1.0, "metadata.units", warnings, float(np.max(np.ptp(vertices_array, axis=1)))
    if raw_units in {"mm", "millimeter", "millimeters"}:
        return 0.001, "metadata.units", warnings, float(np.max(np.ptp(vertices_array, axis=1)))
    if raw_units:
        warnings.append(
            f"mesh.metadata.units='{raw_units}' is unsupported; falling back to heuristic unit detection."
        )

    max_extent = float(np.max(np.ptp(vertices_array, axis=1)))
    if max_extent > 5.0:
        return 0.001, "heuristic:max_extent_gt_5", warnings, max_extent
    if max_extent < 2.0:
        return 1.0, "heuristic:max_extent_lt_2", warnings, max_extent

    warnings.append(
        f"Mesh extent {max_extent:.3f} is ambiguous for unit detection; defaulting to millimeters."
    )
    return 0.001, "heuristic:ambiguous_default_mm", warnings, max_extent


def prepare_mesh(
    vertices: List[float],
    indices: List[int],
    surface_tags: List[int] = None,
    boundary_conditions: Dict = None,
    mesh_metadata: Optional[Dict[str, Any]] = None,
) -> Dict:
    """
    Convert vertex/index arrays to bempp grid with boundary conditions

    Args:
        vertices: Flat list of vertex coordinates [x0,y0,z0, x1,y1,z1, ...]
        indices: Flat list of triangle indices [i0,i1,i2, i3,i4,i5, ...]
        surface_tags: Per-triangle surface tags (1=wall, 2=source, 3=secondary, 4=interface)
        boundary_conditions: Boundary condition definitions
        mesh_metadata: Optional metadata with units/unitScaleToMeter hints

    Returns:
        dict containing bempp grid and boundary info, including:
        - grid: bempp grid object
        - original_vertices: (3, N) array - preserved for symmetry detection
        - original_indices: (3, M) array - preserved for symmetry detection
        - original_surface_tags: (M,) array - preserved for symmetry detection
        - throat_elements, wall_elements, mouth_elements: element indices per boundary
    """
    logger.debug("[BEM prepare_mesh] Called with %d vertex values, %d index values", len(vertices), len(indices))
    logger.debug("[BEM prepare_mesh] surface_tags provided: %s", surface_tags is not None)
    if surface_tags is not None:
        logger.debug("[BEM prepare_mesh] surface_tags length: %d", len(surface_tags))

    # Reshape vertices to (3, N) array
    vertices_array = np.array(vertices).reshape(-1, 3).T
    num_vertices = vertices_array.shape[1]
    logger.debug("[BEM prepare_mesh] Reshaped vertices: %s -> %d vertices", vertices_array.shape, num_vertices)

    if num_vertices == 0:
        raise ValueError(
            "Mesh has no vertices. The mesh payload must contain at least one vertex "
            "(vertices list is empty)."
        )

    # Reshape indices to (3, M) array
    indices_array = np.array(indices, dtype=np.int32).reshape(-1, 3).T
    num_triangles = indices_array.shape[1]
    logger.debug("[BEM prepare_mesh] Reshaped indices: %s -> %d triangles", indices_array.shape, num_triangles)

    if num_triangles == 0:
        raise ValueError(
            "Mesh has no triangles. The mesh payload must contain at least one triangle "
            "(indices list is empty)."
        )

    # Validate indices are within bounds
    max_index = int(np.max(indices_array))
    min_index = int(np.min(indices_array))
    logger.debug("[BEM prepare_mesh] Index range: [%d, %d]", min_index, max_index)
    if max_index >= num_vertices:
        raise ValueError(
            f"Mesh index out of bounds: max index {max_index} >= vertex count {num_vertices}. "
            f"This indicates corrupted mesh data from the frontend."
        )
    if min_index < 0:
        raise ValueError(
            f"Mesh has negative index: {min_index}. "
            f"This indicates corrupted mesh data from the frontend."
        )

    logger.debug(
        "[BEM] Mesh validated: %d vertices, %d triangles, index range [%d, %d]",
        num_vertices, indices_array.shape[1], min_index, max_index,
    )

    unit_scale_to_meter, unit_source, unit_warnings, max_extent = _resolve_unit_scale_to_meter(
        vertices_array, mesh_metadata
    )
    vertices_array = vertices_array * unit_scale_to_meter
    logger.debug(
        "[BEM] Unit normalization: source=%s, scale=%g, input_extent=%.4f",
        unit_source, unit_scale_to_meter, max_extent,
    )
    for warning in unit_warnings:
        logger.warning("[BEM] Unit warning: %s", warning)

    # Store original mesh for symmetry detection (before any refinement)
    original_vertices = vertices_array.copy()
    original_indices = indices_array.copy()

    # Create domain indices from surface tags if provided
    if surface_tags is not None:
        domain_indices = np.array(surface_tags, dtype=np.int32)
        if domain_indices.shape[0] != num_triangles:
            raise ValueError(
                f"surface_tags length {domain_indices.shape[0]} does not match triangle count {num_triangles}."
            )
        original_surface_tags = domain_indices.copy()
    else:
        # Default: all elements are rigid walls (tag 1)
        domain_indices = np.full(indices_array.shape[1], 1, dtype=np.int32)
        original_surface_tags = domain_indices.copy()

    # Create bempp grid with domain indices
    if np.count_nonzero(domain_indices == 2) == 0:
        raise ValueError("Mesh has no source-tagged elements (tag 2).")

    grid = bempp_api.Grid(vertices_array, indices_array, domain_indices)

    # Store boundary info with the grid
    # IMPORTANT: Include original mesh data for symmetry detection
    return {
        'grid': grid,
        'surface_tags': domain_indices,
        'boundary_conditions': boundary_conditions
        or {
            'throat': {'type': 'velocity', 'surfaceTag': 2, 'value': 1.0},
            'wall': {'type': 'neumann', 'surfaceTag': 1, 'value': 0.0},
            'mouth': {'type': 'robin', 'surfaceTag': 1, 'impedance': 'spherical'}
        },
        'throat_elements': np.where(domain_indices == 2)[0],
        'wall_elements': np.where(domain_indices == 1)[0],
        'mouth_elements': np.where(domain_indices == 3)[0],
        'mesh_metadata': mesh_metadata or {},
        'unit_scale_to_meter': unit_scale_to_meter,
        'unit_detection': {
            'source': unit_source,
            'input_max_extent': max_extent,
            'warnings': unit_warnings,
        },
        # Preserve original mesh for symmetry detection
        'original_vertices': original_vertices,
        'original_indices': original_indices,
        'original_surface_tags': original_surface_tags
    }


def load_msh_for_bem(
    msh_path: str,
    merge_tol: float = 1e-9,
    area_tol: float = 0.0,
    scale_factor: float = 0.001,
) -> Dict:
    """
    Load a Gmsh .msh surface file, clean it with mesh_cleaner, and prepare it
    for BEM simulation via HornBEMSolver.

    This replaces the waveguide_builder-generated mesh path when working directly
    with .msh files produced by Gmsh outside the OCC pipeline.

    Args:
        msh_path: Path to .msh file (vertices assumed in mm unless scale_factor overrides).
        merge_tol: Vertex merge tolerance in mesh file units (default 1e-9).
        area_tol: Minimum triangle area; smaller faces removed (default 0.0).
        scale_factor: Multiply raw mesh coordinates to convert to metres (default 0.001 = mm).

    Returns:
        Dict as returned by prepare_mesh(), compatible with HornBEMSolver and BEMSolver.solve().
    """
    cleaned_mesh, changes, stats_before, stats_after = _clean_load_msh(
        msh_path, merge_tol=merge_tol, area_tol=area_tol
    )

    import meshio as _meshio
    from .mesh_cleaner import _find_triangle_block as _ftb
    _, triangles_np = _ftb(cleaned_mesh)
    vertices_np = np.asarray(cleaned_mesh.points, dtype=float)  # (N, 3)
    physical_tags = extract_physical_tags(cleaned_mesh)  # (M,) or None

    # Validate physical tags exist - BEM requires source-tagged elements
    if physical_tags is None:
        raise ValueError(
            "Mesh file has no physical groups (gmsh:physical). "
            "BEM simulation requires surface tags: 1=wall, 2=source, 3=secondary, 4=interface. "
            "Ensure the .msh file was exported with physical group definitions."
        )
    if 2 not in physical_tags:
        raise ValueError(
            "Mesh file has no source-tagged elements (tag 2). "
            "BEM simulation requires at least one source surface element."
        )

    # Convert to column-major (3, N) / (3, M) for bempp
    vertices_col = (vertices_np * scale_factor).T  # (3, N)
    elements_col = triangles_np.T.astype(np.int32)  # (3, M)

    surface_tags_list = physical_tags.tolist()

    logger.info(
        "[load_msh_for_bem] Loaded %s — %d verts, %d tris (after cleaning). "
        "Changes: %s",
        msh_path, stats_after.vertices, stats_after.triangles, changes,
    )

    # Delegate to prepare_mesh for validation, bempp grid creation, and dict assembly.
    return prepare_mesh(
        vertices=vertices_col.T.ravel().tolist(),   # flat [x0,y0,z0, x1,y1,z1, ...]
        indices=elements_col.T.ravel().tolist(),    # flat [i0,i1,i2, i3,i4,i5, ...]
        surface_tags=surface_tags_list,
        mesh_metadata={"unitScaleToMeter": 1.0},   # already scaled above
    )
