import numpy as np
from typing import Dict, List, Optional, Tuple

from .deps import BEMPP_AVAILABLE, bempp_api
from .device_interface import selected_device_metadata
from .mesh import refine_mesh_with_gmsh, prepare_mesh
from .solve import solve, solve_frequency
from .solve_optimized import solve_optimized  # NEW: Optimized solver
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
        # Custom BEMPP parameters removed to use standard configuration
        pass

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
        mesh_metadata: Dict = None,
        use_gmsh: bool = False,
        target_frequency: float = 1000.0
    ) -> Dict:
        return prepare_mesh(
            vertices,
            indices,
            surface_tags,
            boundary_conditions,
            mesh_metadata,
            use_gmsh,
            target_frequency,
        )

    def solve(
        self,
        mesh,
        frequency_range: List[float],
        num_frequencies: int,
        sim_type: str,
        polar_config: Optional[Dict] = None,
        progress_callback: Optional[callable] = None,
        stage_callback: Optional[callable] = None,
        use_optimized: bool = True,
        enable_symmetry: bool = True,
        verbose: bool = False,
        mesh_validation_mode: str = "warn",
        frequency_spacing: str = "linear",
    ) -> Dict:
        """
        Run BEM simulation with optional optimizations.

        Args:
            mesh: Mesh dictionary from prepare_mesh
            frequency_range: [start_freq, end_freq] in Hz
            num_frequencies: Number of frequency points
            sim_type: Simulation type
            polar_config: Polar directivity configuration
            progress_callback: Progress callback function
            use_optimized: Use optimized solver with symmetry, caching, correct polars (default: True)
            enable_symmetry: Enable automatic symmetry detection and reduction (default: True)
            verbose: Print detailed progress and validation reports (default: False)

        Returns:
            Results dictionary with simulation data and metadata
        """
        device_info = selected_device_metadata()
        selected = device_info.get("selected", "unknown")
        fallback_reason = device_info.get("fallback_reason")
        if fallback_reason:
            print(f"[BEM] Device interface: {selected} (requested=opencl, reason: {fallback_reason})")
        else:
            print(f"[BEM] Device interface: {selected} (requested=opencl)")

        if use_optimized:
            return solve_optimized(
                mesh, frequency_range, num_frequencies, sim_type,
                polar_config, progress_callback, stage_callback,
                enable_symmetry, verbose=verbose,
                mesh_validation_mode=mesh_validation_mode,
                frequency_spacing=frequency_spacing,
            )
        else:
            # Legacy solver (no symmetry, analytical piston directivity)
            return solve(
                mesh, frequency_range, num_frequencies, sim_type,
                polar_config, progress_callback, stage_callback,
                mesh_validation_mode=mesh_validation_mode,
                frequency_spacing=frequency_spacing,
            )

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
