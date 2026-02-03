import numpy as np
from typing import Dict, List, Optional, Tuple

from .deps import GMSH_AVAILABLE, bempp_api, gmsh


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
        # Initialize Gmsh
        gmsh.initialize()
        gmsh.option.setNumber("General.Terminal", 0)  # Suppress output
        gmsh.model.add("horn_mesh")

        # Calculate target element size based on wavelength
        # Rule of thumb: 6-10 elements per wavelength
        c = 343.0  # Speed of sound m/s
        wavelength = c / target_frequency * 1000  # Convert to mm
        target_size = wavelength / 8  # 8 elements per wavelength

        print(f"[Gmsh] Target element size: {target_size:.1f} mm (for {target_frequency} Hz)")

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
            throat_surfs = [triangle_surfaces[i] for i in range(num_triangles) if surface_tags[i] == 1]
            wall_surfs = [triangle_surfaces[i] for i in range(num_triangles) if surface_tags[i] == 2]
            mouth_surfs = [triangle_surfaces[i] for i in range(num_triangles) if surface_tags[i] == 3]

            if throat_surfs:
                gmsh.model.addPhysicalGroup(2, throat_surfs, 1, "Throat")
            if wall_surfs:
                gmsh.model.addPhysicalGroup(2, wall_surfs, 2, "Walls")
            if mouth_surfs:
                gmsh.model.addPhysicalGroup(2, mouth_surfs, 3, "Mouth")

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
            gmsh.finalize()
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
            gmsh.finalize()
            return vertices, indices, surface_tags

        # Generate new surface tags based on physical groups
        num_refined_tris = refined_indices.shape[1]
        refined_tags = np.full(num_refined_tris, 2, dtype=np.int32)  # Default: wall

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

        gmsh.finalize()
        return refined_vertices, refined_indices, refined_tags

    except Exception as e:
        print(f"[Gmsh] Error during mesh refinement: {e}")
        if gmsh.isInitialized():
            gmsh.finalize()
        return vertices, indices, surface_tags


def prepare_mesh(
    vertices: List[float],
    indices: List[int],
    surface_tags: List[int] = None,
    boundary_conditions: Dict = None,
    use_gmsh: bool = False,
    target_frequency: float = 1000.0
) -> Dict:
    """
    Convert vertex/index arrays to bempp grid with boundary conditions

    Args:
        vertices: Flat list of vertex coordinates [x0,y0,z0, x1,y1,z1, ...]
        indices: Flat list of triangle indices [i0,i1,i2, i3,i4,i5, ...]
        surface_tags: Per-triangle surface tags (1=throat, 2=wall, 3=mouth)
        boundary_conditions: Boundary condition definitions
        use_gmsh: If True, use Gmsh to refine the mesh for better BEM accuracy
        target_frequency: Target frequency for mesh element sizing (Hz)

    Returns:
        dict containing bempp grid and boundary info
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

    # Create domain indices from surface tags if provided
    if surface_tags is not None:
        domain_indices = np.array(surface_tags, dtype=np.int32)
    else:
        # Default: all elements are wall (tag 2)
        domain_indices = np.full(indices_array.shape[1], 2, dtype=np.int32)

    # Optionally refine mesh with Gmsh
    if use_gmsh and GMSH_AVAILABLE:
        vertices_array, indices_array, domain_indices = refine_mesh_with_gmsh(
            vertices_array, indices_array, domain_indices, target_frequency
        )

    # Create bempp grid with domain indices
    grid = bempp_api.Grid(vertices_array, indices_array, domain_indices)

    # Store boundary info with the grid
    return {
        'grid': grid,
        'surface_tags': domain_indices,
        'boundary_conditions': boundary_conditions
        or {
            'throat': {'type': 'velocity', 'surfaceTag': 1, 'value': 1.0},
            'wall': {'type': 'neumann', 'surfaceTag': 2, 'value': 0.0},
            'mouth': {'type': 'robin', 'surfaceTag': 3, 'impedance': 'spherical'}
        },
        'throat_elements': np.where(domain_indices == 1)[0],
        'wall_elements': np.where(domain_indices == 2)[0],
        'mouth_elements': np.where(domain_indices == 3)[0]
    }
