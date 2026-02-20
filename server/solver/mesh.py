import numpy as np
from typing import Any, Dict, List, Optional, Tuple

from .deps import GMSH_AVAILABLE, bempp_api, gmsh
from .gmsh_utils import gmsh_lock


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


def refine_mesh_with_gmsh(
    vertices: np.ndarray,
    indices: np.ndarray,
    surface_tags: np.ndarray = None,
    target_frequency: float = 1000.0
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Use Gmsh to refine and improve mesh quality for BEM simulation.

    Args:
        vertices: (3, N) array of vertex coordinates
        indices: (3, M) array of triangle indices
        surface_tags: (M,) array of surface tags per triangle
        target_frequency: Target frequency for element sizing (Hz)

    Returns:
        Tuple of (refined_vertices, refined_indices, refined_tags)
    """
    if not GMSH_AVAILABLE:
        print("[Gmsh] Not available, using original mesh")
        return vertices, indices, surface_tags

    try:
        with gmsh_lock:
            initialized_here = False
            try:
                if not gmsh.isInitialized():
                    gmsh.initialize()
                    initialized_here = True
                gmsh.option.setNumber("General.Terminal", 0)  # Suppress output
                gmsh.clear()
                gmsh.model.add("horn_mesh")

                # Calculate target element size based on wavelength
                # Rule of thumb: 6-10 elements per wavelength
                c = 343.0  # Speed of sound m/s
                wavelength = c / target_frequency
                target_size = wavelength / 8  # 8 elements per wavelength
                print(f"[Gmsh] Target element size: {target_size:.6f} m (for {target_frequency} Hz)")

                # Add vertices to Gmsh
                num_vertices = vertices.shape[1]
                vertex_tags = []
                for i in range(num_vertices):
                    tag = gmsh.model.geo.addPoint(
                        vertices[0, i], vertices[1, i], vertices[2, i], target_size
                    )
                    vertex_tags.append(tag)

                # Create surface loops from triangles
                # Group triangles by surface tag for physical groups
                num_triangles = indices.shape[1]
                triangle_surfaces = []

                for i in range(num_triangles):
                    v0, v1, v2 = indices[0, i], indices[1, i], indices[2, i]

                    # Create lines for this triangle
                    l1 = gmsh.model.geo.addLine(vertex_tags[v0], vertex_tags[v1])
                    l2 = gmsh.model.geo.addLine(vertex_tags[v1], vertex_tags[v2])
                    l3 = gmsh.model.geo.addLine(vertex_tags[v2], vertex_tags[v0])

                    # Create curve loop and surface
                    loop = gmsh.model.geo.addCurveLoop([l1, l2, l3])
                    surf = gmsh.model.geo.addPlaneSurface([loop])
                    triangle_surfaces.append(surf)

                gmsh.model.geo.synchronize()

                # Add physical groups for boundary conditions
                if surface_tags is not None:
                    # Canonical tag contract:
                    # 1 = walls, 2 = source, 3 = secondary domain, 4 = interface/symmetry
                    wall_surfs = [triangle_surfaces[i] for i in range(num_triangles) if surface_tags[i] == 1]
                    source_surfs = [triangle_surfaces[i] for i in range(num_triangles) if surface_tags[i] == 2]
                    secondary_surfs = [triangle_surfaces[i] for i in range(num_triangles) if surface_tags[i] == 3]
                    interface_surfs = [triangle_surfaces[i] for i in range(num_triangles) if surface_tags[i] == 4]

                    if wall_surfs:
                        gmsh.model.addPhysicalGroup(2, wall_surfs, 1, "SD1G0")
                    if source_surfs:
                        gmsh.model.addPhysicalGroup(2, source_surfs, 2, "SD1D1001")
                    if secondary_surfs:
                        gmsh.model.addPhysicalGroup(2, secondary_surfs, 3, "SD2G0")
                    if interface_surfs:
                        gmsh.model.addPhysicalGroup(2, interface_surfs, 4, "I1-2")

                # Set mesh options
                gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal-Delaunay
                gmsh.option.setNumber("Mesh.ElementOrder", 1)  # Linear elements
                gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)  # Optimize mesh

                # Generate 2D mesh
                gmsh.model.mesh.generate(2)

                # Extract refined mesh
                node_tags, node_coords, _ = gmsh.model.mesh.getNodes()

                # Reshape node coordinates
                refined_vertices = np.array(node_coords).reshape(-1, 3).T

                # Get elements
                elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements(2)

                if len(elem_types) == 0:
                    print("[Gmsh] No elements generated, using original mesh")
                    return vertices, indices, surface_tags

                # Find triangles (type 2)
                refined_indices = None
                for i, etype in enumerate(elem_types):
                    if etype == 2:  # Triangle
                        # Node tags are 1-indexed in Gmsh
                        tri_nodes = np.array(elem_node_tags[i]).reshape(-1, 3) - 1
                        refined_indices = tri_nodes.T.astype(np.int32)
                        break

                if refined_indices is None:
                    print("[Gmsh] No triangles found, using original mesh")
                    return vertices, indices, surface_tags

                # Generate new surface tags based on physical groups
                num_refined_tris = refined_indices.shape[1]
                refined_tags = np.full(num_refined_tris, 1, dtype=np.int32)  # Default: wall

                # Get physical group assignments
                for dim, tag in gmsh.model.getPhysicalGroups(2):
                    entities = gmsh.model.getEntitiesForPhysicalGroup(dim, tag)
                    for entity in entities:
                        elem_types_e, elem_tags_e, _ = gmsh.model.mesh.getElements(2, entity)
                        for et, tags in zip(elem_types_e, elem_tags_e):
                            if et == 2:
                                for elem_tag in tags:
                                    # Find element index (elem_tags are 1-indexed)
                                    idx = np.where(np.array(elem_tags[0]) == elem_tag)[0]
                                    if len(idx) > 0:
                                        refined_tags[idx[0]] = tag

                print(
                    f"[Gmsh] Mesh refined: {num_vertices} -> {refined_vertices.shape[1]} vertices, "
                    f"{num_triangles} -> {num_refined_tris} triangles"
                )

                return refined_vertices, refined_indices, refined_tags
            finally:
                if initialized_here and gmsh.isInitialized():
                    gmsh.finalize()
    except Exception as e:
        print(f"[Gmsh] Error during mesh refinement: {e}")
        return vertices, indices, surface_tags


def prepare_mesh(
    vertices: List[float],
    indices: List[int],
    surface_tags: List[int] = None,
    boundary_conditions: Dict = None,
    mesh_metadata: Optional[Dict[str, Any]] = None,
    use_gmsh: bool = False,
    target_frequency: float = 1000.0
) -> Dict:
    """
    Convert vertex/index arrays to bempp grid with boundary conditions

    Args:
        vertices: Flat list of vertex coordinates [x0,y0,z0, x1,y1,z1, ...]
        indices: Flat list of triangle indices [i0,i1,i2, i3,i4,i5, ...]
        surface_tags: Per-triangle surface tags (1=wall, 2=source, 3=secondary, 4=interface)
        boundary_conditions: Boundary condition definitions
        mesh_metadata: Optional metadata with units/unitScaleToMeter hints
        use_gmsh: If True, use Gmsh to refine the mesh for better BEM accuracy
        target_frequency: Target frequency for mesh element sizing (Hz)

    Returns:
        dict containing bempp grid and boundary info, including:
        - grid: bempp grid object
        - original_vertices: (3, N) array - preserved for symmetry detection
        - original_indices: (3, M) array - preserved for symmetry detection
        - original_surface_tags: (M,) array - preserved for symmetry detection
        - throat_elements, wall_elements, mouth_elements: element indices per boundary
    """
    print(f"[BEM prepare_mesh] Called with {len(vertices)} vertex values, {len(indices)} index values")
    print(f"[BEM prepare_mesh] surface_tags provided: {surface_tags is not None}")
    if surface_tags is not None:
        print(f"[BEM prepare_mesh] surface_tags length: {len(surface_tags)}")

    # Reshape vertices to (3, N) array
    vertices_array = np.array(vertices).reshape(-1, 3).T
    num_vertices = vertices_array.shape[1]
    print(f"[BEM prepare_mesh] Reshaped vertices: {vertices_array.shape} -> {num_vertices} vertices")

    # Reshape indices to (3, M) array
    indices_array = np.array(indices, dtype=np.int32).reshape(-1, 3).T
    num_triangles = indices_array.shape[1]
    print(f"[BEM prepare_mesh] Reshaped indices: {indices_array.shape} -> {num_triangles} triangles")

    # Validate indices are within bounds
    max_index = int(np.max(indices_array))
    min_index = int(np.min(indices_array))
    print(f"[BEM prepare_mesh] Index range: [{min_index}, {max_index}]")
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

    print(
        f"[BEM] Mesh validated: {num_vertices} vertices, {indices_array.shape[1]} triangles, "
        f"index range [{min_index}, {max_index}]"
    )

    unit_scale_to_meter, unit_source, unit_warnings, max_extent = _resolve_unit_scale_to_meter(
        vertices_array, mesh_metadata
    )
    vertices_array = vertices_array * unit_scale_to_meter
    print(
        f"[BEM] Unit normalization: source={unit_source}, "
        f"scale={unit_scale_to_meter:g}, input_extent={max_extent:.4f}"
    )
    for warning in unit_warnings:
        print(f"[BEM] Unit warning: {warning}")

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

    # Optionally refine mesh with Gmsh
    if use_gmsh and GMSH_AVAILABLE:
        vertices_array, indices_array, domain_indices = refine_mesh_with_gmsh(
            vertices_array, indices_array, domain_indices, target_frequency
        )

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
