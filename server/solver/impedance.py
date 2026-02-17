"""
Throat acoustic impedance via area-weighted pressure integration.

Reference: Galucha BEMPP Ath4 Solver Prerelease, HornBEMSolver._calculate_impedance()
"""

import numpy as np


def _pressure_on_throat_elements(grid, pressure_solution, throat_elements):
    """Return complex pressure sampled per throat element."""
    # Preferred path for bempp-cl grid functions: evaluate directly on
    # element centers, which avoids assuming coefficient index == vertex index.
    if hasattr(pressure_solution, "evaluate_on_element_centers"):
        center_values = np.asarray(pressure_solution.evaluate_on_element_centers())
        if center_values.size == 0:
            return np.array([], dtype=np.complex128)
        center_values = np.reshape(center_values, (-1, center_values.shape[-1]))
        return center_values[0, throat_elements]

    if hasattr(pressure_solution, "coefficients"):
        coeffs = np.asarray(pressure_solution.coefficients)
    else:
        coeffs = np.asarray(pressure_solution)

    coeffs = np.reshape(coeffs, (-1,))
    elements = grid.elements  # (3, num_triangles)
    throat_vertex_indices = elements[:, throat_elements]  # (3, num_throat_tris)
    max_required = int(np.max(throat_vertex_indices))
    if max_required >= coeffs.shape[0]:
        raise ValueError(
            f"Pressure coefficient array length {coeffs.shape[0]} is smaller than "
            f"required vertex index {max_required}."
        )
    p_at_vertices = coeffs[throat_vertex_indices]
    return np.mean(p_at_vertices, axis=0)


def calculate_throat_impedance(grid, pressure_solution, throat_elements):
    """
    Calculate throat impedance from BEM surface pressure solution.

    Integrates complex pressure over throat surface triangles using
    area weighting.  Z = F / (u * S_throat).  Since source velocity
    u = 1 m/s, the total force *is* the impedance times the total
    throat area.

    Args:
        grid: bempp grid with .vertices (3, N), .elements (3, M), .volumes (M,)
        pressure_solution: bempp GridFunction (preferred) or coefficient array
        throat_elements: Array of triangle indices belonging to throat (tag 2)

    Returns:
        complex: Impedance as complex(Re(force), -Im(force))
    """
    if throat_elements is None or len(throat_elements) == 0:
        return complex(0.0, 0.0)

    areas = grid.volumes           # (num_triangles,)
    p_avg = _pressure_on_throat_elements(grid, pressure_solution, throat_elements)

    # Area-weighted force integral
    throat_areas = areas[throat_elements]                   # (num_throat_tris,)
    total_force = np.sum(p_avg * throat_areas) * 10

    return complex(np.real(total_force), -np.imag(total_force))
