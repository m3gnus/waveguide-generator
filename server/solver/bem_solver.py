import numpy as np
from typing import Dict, List, Optional, Tuple

from .deps import BEMPP_AVAILABLE, bempp_api
from .mesh import refine_mesh_with_gmsh, prepare_mesh
from .solve import solve, solve_frequency
from .directivity import (
    calculate_directivity_index_from_pressure,
    calculate_directivity_patterns,
    piston_directivity
)


class BEMSolver:
    """
    BEM acoustic solver for horn simulations
    """

    def __init__(self):
        if not BEMPP_AVAILABLE:
            raise ImportError("bempp-cl is not installed. Please install it first.")

        # Set bempp options for better performance
        # API changed between bempp-cl versions:
        # - Older versions use hmat (H-matrices)
        # - Newer versions (0.3+) use fmm (Fast Multipole Method)
        try:
            if hasattr(bempp_api, 'GLOBAL_PARAMETERS'):
                params = bempp_api.GLOBAL_PARAMETERS
                # Newer bempp-cl uses FMM instead of H-matrices
                if hasattr(params, 'fmm'):
                    params.fmm.expansion_order = 5  # Balance accuracy/speed
                # Older versions use hmat
                elif hasattr(params, 'hmat'):
                    params.hmat.eps = 1e-3
                    if hasattr(params.assembly, 'boundary_operator_assembly_type'):
                        params.assembly.boundary_operator_assembly_type = 'hmat'
        except Exception as e:
            # If parameter setting fails, continue with defaults
            print(f"Note: Could not set bempp parameters (using defaults): {e}")

    def refine_mesh_with_gmsh(
        self,
        vertices: np.ndarray,
        indices: np.ndarray,
        surface_tags: np.ndarray = None,
        target_frequency: float = 1000.0
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        return refine_mesh_with_gmsh(vertices, indices, surface_tags, target_frequency)

    def prepare_mesh(
        self,
        vertices: List[float],
        indices: List[int],
        surface_tags: List[int] = None,
        boundary_conditions: Dict = None,
        use_gmsh: bool = False,
        target_frequency: float = 1000.0
    ) -> Dict:
        return prepare_mesh(vertices, indices, surface_tags, boundary_conditions, use_gmsh, target_frequency)

    def solve(
        self,
        mesh,
        frequency_range: List[float],
        num_frequencies: int,
        sim_type: str,
        polar_config: Optional[Dict] = None,
        progress_callback: Optional[callable] = None
    ) -> Dict:
        return solve(mesh, frequency_range, num_frequencies, sim_type, polar_config, progress_callback)

    def _solve_frequency(
        self,
        grid,
        k: float,
        c: float,
        rho: float,
        sim_type: str,
        throat_elements: np.ndarray = None
    ) -> Tuple[float, complex, float]:
        return solve_frequency(grid, k, c, rho, sim_type, throat_elements)

    def _calculate_directivity_index_from_pressure(
        self,
        grid,
        k: float,
        c: float,
        rho: float,
        p_total,
        u_total,
        space_p,
        space_u,
        omega: float,
        spl_on_axis: float
    ) -> float:
        return calculate_directivity_index_from_pressure(
            grid, k, c, rho, p_total, u_total, space_p, space_u, omega, spl_on_axis
        )

    def _piston_directivity(self, ka: float, sin_theta: float) -> float:
        return piston_directivity(ka, sin_theta)

    def _calculate_directivity_patterns(
        self,
        grid,
        frequencies: np.ndarray,
        c: float,
        rho: float,
        sim_type: str,
        polar_config: Optional[Dict] = None
    ) -> Dict[str, List[List[float]]]:
        return calculate_directivity_patterns(grid, frequencies, c, rho, sim_type, polar_config)
