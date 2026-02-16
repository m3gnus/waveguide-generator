"""
Throat acoustic impedance via area-weighted pressure integration.

Reference: Galucha BEMPP Ath4 Solver Prerelease, HornBEMSolver._calculate_impedance()
"""

import numpy as np


def calculate_throat_impedance(grid, p_total_coefficients, throat_elements):
    """
    Calculate throat impedance from BEM surface pressure solution.

    Integrates complex pressure over throat surface triangles using
    area weighting.  Z = F / (u * S_throat).  Since source velocity
    u = 1 m/s, the total force *is* the impedance times the total
    throat area.

    Args:
        grid: bempp grid with .vertices (3, N), .elements (3, M), .volumes (M,)
        p_total_coefficients: Complex P1 coefficient array from BEM solve
        throat_elements: Array of triangle indices belonging to throat (tag 2)

    Returns:
        complex: Impedance as complex(Re(force), -Im(force))
    """
    if throat_elements is None or len(throat_elements) == 0:
        return complex(0.0, 0.0)

    coeffs = np.asarray(p_total_coefficients)
    elements = grid.elements       # (3, num_triangles)
    areas = grid.volumes           # (num_triangles,)

    # Vertex indices of each throat triangle
    throat_vertex_indices = elements[:, throat_elements]   # (3, num_throat_tris)

    # Average complex pressure at each throat triangle's vertices
    p_at_vertices = coeffs[throat_vertex_indices]          # (3, num_throat_tris)
    p_avg = np.mean(p_at_vertices, axis=0)                 # (num_throat_tris,)

    # Area-weighted force integral
    throat_areas = areas[throat_elements]                   # (num_throat_tris,)
    total_force = np.sum(p_avg * throat_areas) * 10

    return complex(np.real(total_force), -np.imag(total_force))
