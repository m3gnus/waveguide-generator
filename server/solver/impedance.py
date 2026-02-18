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
    Specific acoustic radiation impedance: Z_s = <p> / u_n [Pa·s/m]

    Area-weighted average pressure over throat divided by source velocity
    (u_n = 1 m/s). Equivalent to (∫p dA) / (u * S_throat).

    At low frequency (ka << 1): Re(Z_s) ≈ 0 (reactive mass-loading).
    At high frequency (ka >> 1): Re(Z_s) / (ρc) → 1 (full radiation resistance).

    Args:
        grid: bempp grid with .vertices (3, N), .elements (3, M), .volumes (M,)
        pressure_solution: bempp GridFunction (preferred) or coefficient array
        throat_elements: Array of triangle indices belonging to throat (tag 2)

    Returns:
        complex: Specific acoustic impedance Z_s [Pa·s/m]
    """
    if throat_elements is None or len(throat_elements) == 0:
        return complex(0.0, 0.0)

    areas = grid.volumes           # (num_triangles,)
    p_avg = _pressure_on_throat_elements(grid, pressure_solution, throat_elements)

    throat_areas = areas[throat_elements]                   # (num_throat_tris,)
    S_throat = np.sum(throat_areas)
    if S_throat == 0:
        return complex(0.0, 0.0)

    total_force = np.sum(p_avg * throat_areas)              # F = ∫p dA [Pa·m² = N]
    Z_specific = total_force / S_throat                     # Z_s = F / (u·S) [Pa·s/m]

    return complex(np.real(Z_specific), np.imag(Z_specific))
