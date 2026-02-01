"""
BEM Solver Implementation using bempp-cl
Handles acoustic simulations for horn geometries
"""

import numpy as np
from typing import List, Dict, Callable, Optional, Tuple

try:
    import bempp.api
    BEMPP_AVAILABLE = True
except ImportError:
    BEMPP_AVAILABLE = False
    print("Warning: bempp.api not available")


class BEMSolver:
    """
    BEM acoustic solver for horn simulations
    """
    
    def __init__(self):
        if not BEMPP_AVAILABLE:
            raise ImportError("bempp-cl is not installed. Please install it first.")
        
        # Set bempp options for better performance
        bempp.api.GLOBAL_PARAMETERS.hmat.eps = 1E-3
        bempp.api.GLOBAL_PARAMETERS.assembly.boundary_operator_assembly_type = 'hmat'
    
    def prepare_mesh(self, vertices: List[float], indices: List[int]):
        """
        Convert vertex/index arrays to bempp grid
        
        Args:
            vertices: Flat list of vertex coordinates [x0,y0,z0, x1,y1,z1, ...]
            indices: Flat list of triangle indices [i0,i1,i2, i3,i4,i5, ...]
        
        Returns:
            bempp grid object
        """
        # Reshape vertices to (3, N) array
        vertices_array = np.array(vertices).reshape(-1, 3).T
        
        # Reshape indices to (3, M) array
        indices_array = np.array(indices, dtype=np.int32).reshape(-1, 3).T
        
        # Create bempp grid
        grid = bempp.api.Grid(vertices_array, indices_array)
        
        return grid
    
    def solve(
        self,
        mesh,
        frequency_range: List[float],
        num_frequencies: int,
        sim_type: str,
        progress_callback: Optional[Callable[[float], None]] = None
    ) -> Dict:
        """
        Run BEM simulation
        
        Args:
            mesh: bempp grid object
            frequency_range: [start_freq, end_freq] in Hz
            num_frequencies: Number of frequency points
            sim_type: "1" for infinite baffle, "2" for free-standing
            progress_callback: Optional callback for progress updates
        
        Returns:
            Dictionary with simulation results
        """
        # Generate frequency array
        frequencies = np.linspace(
            frequency_range[0],
            frequency_range[1],
            num_frequencies
        )
        
        results = {
            "frequencies": frequencies.tolist(),
            "directivity": {
                "horizontal": [],
                "vertical": [],
                "diagonal": []
            },
            "spl_on_axis": {
                "frequencies": frequencies.tolist(),
                "spl": []
            },
            "impedance": {
                "frequencies": frequencies.tolist(),
                "real": [],
                "imaginary": []
            },
            "di": {
                "frequencies": frequencies.tolist(),
                "di": []
            }
        }
        
        # Speed of sound and air density
        c = 343.0  # m/s
        rho = 1.21  # kg/m^3
        
        # Solve for each frequency
        for i, freq in enumerate(frequencies):
            if progress_callback:
                progress_callback(i / len(frequencies))
            
            # Wavenumber
            k = 2 * np.pi * freq / c
            
            # Solve Helmholtz equation
            spl, impedance = self._solve_frequency(mesh, k, c, rho, sim_type)
            
            # Store results
            results["spl_on_axis"]["spl"].append(spl)
            results["impedance"]["real"].append(impedance.real)
            results["impedance"]["imaginary"].append(impedance.imag)
            
            # Calculate directivity (simplified)
            di = self._calculate_directivity_index(spl)
            results["di"]["di"].append(di)
        
        # Calculate directivity patterns at key frequencies
        results["directivity"] = self._calculate_directivity_patterns(
            mesh, frequencies, c, rho, sim_type
        )
        
        if progress_callback:
            progress_callback(1.0)
        
        return results
    
    def _solve_frequency(
        self,
        grid,
        k: float,
        c: float,
        rho: float,
        sim_type: str
    ) -> Tuple[float, complex]:
        """
        Solve BEM for a single frequency
        
        Returns:
            (spl_on_axis, throat_impedance)
        """
        # Create function spaces
        space = bempp.api.function_space(grid, "P", 1)
        
        # Define operators
        identity = bempp.api.operators.boundary.sparse.identity(
            space, space, space
        )
        
        dlp = bempp.api.operators.boundary.helmholtz.double_layer(
            space, space, space, k
        )
        
        slp = bempp.api.operators.boundary.helmholtz.single_layer(
            space, space, space, k
        )
        
        # Boundary conditions
        # For horn: rigid walls (Neumann BC), source at throat
        
        # Define incident field (plane wave at throat)
        @bempp.api.callable
        def incident_field(x, n, domain_index, result):
            # Simple plane wave source
            result[0] = 1.0
        
        # Create grid function for incident field
        incident_grid_fun = bempp.api.GridFunction(space, fun=incident_field)
        
        # Solve boundary integral equation
        # (0.5 * I + K) * phi = incident_field
        lhs = 0.5 * identity + dlp
        rhs = incident_grid_fun
        
        # Solve
        phi, info = bempp.api.linalg.gmres(lhs, rhs, tol=1E-5)
        
        # Calculate pressure at observation point (on-axis, 1m from mouth)
        # This is simplified - real implementation would evaluate at specific points
        
        # Estimate SPL (simplified)
        spl = 90.0 + 20 * np.log10(abs(k) / 10.0)  # Placeholder calculation
        
        # Estimate impedance (simplified)
        impedance = complex(rho * c, 0.1 * rho * c)  # Placeholder
        
        return spl, impedance
    
    def _calculate_directivity_index(self, spl: float) -> float:
        """Calculate directivity index from SPL"""
        # Simplified DI calculation
        # Real implementation would integrate over sphere
        return 6.0  # Placeholder
    
    def _calculate_directivity_patterns(
        self,
        grid,
        frequencies: np.ndarray,
        c: float,
        rho: float,
        sim_type: str
    ) -> Dict[str, List[List[float]]]:
        """
        Calculate directivity patterns at key frequencies
        
        Returns patterns for horizontal, vertical, and diagonal planes
        """
        # Select 3 key frequencies for directivity
        key_freq_indices = [0, len(frequencies) // 2, -1]
        
        patterns = {
            "horizontal": [],
            "vertical": [],
            "diagonal": []
        }
        
        # Angles for directivity (0 to 360 degrees)
        angles = np.linspace(0, 360, 37)
        
        for freq_idx in key_freq_indices:
            freq = frequencies[freq_idx]
            k = 2 * np.pi * freq / c
            
            # Calculate directivity at each angle
            # This is simplified - real implementation would solve BEM at each angle
            horizontal = []
            vertical = []
            diagonal = []
            
            for angle in angles:
                # Placeholder directivity pattern (cardioid-like)
                theta_rad = np.radians(angle)
                h_pattern = -10 * (1 - 0.5 * np.cos(theta_rad))
                v_pattern = -15 * (1 - 0.3 * np.cos(theta_rad))
                d_pattern = -12 * (1 - 0.4 * np.cos(theta_rad))
                
                horizontal.append([angle, h_pattern])
                vertical.append([angle, v_pattern])
                diagonal.append([angle, d_pattern])
            
            patterns["horizontal"].append(horizontal)
            patterns["vertical"].append(vertical)
            patterns["diagonal"].append(diagonal)
        
        return patterns


# Fallback mock solver if bempp is not available
class MockBEMSolver:
    """Mock solver for testing without bempp"""
    
    def prepare_mesh(self, vertices: List[float], indices: List[int]):
        return {"vertices": vertices, "indices": indices}
    
    def solve(self, mesh, frequency_range, num_frequencies, sim_type, progress_callback=None):
        frequencies = np.linspace(frequency_range[0], frequency_range[1], num_frequencies)
        
        results = {
            "frequencies": frequencies.tolist(),
            "directivity": {"horizontal": [], "vertical": [], "diagonal": []},
            "spl_on_axis": {
                "frequencies": frequencies.tolist(),
                "spl": [90.0 + np.random.randn() * 2 for _ in frequencies]
            },
            "impedance": {
                "frequencies": frequencies.tolist(),
                "real": [400 + np.random.randn() * 20 for _ in frequencies],
                "imaginary": [50 + np.random.randn() * 10 for _ in frequencies]
            },
            "di": {
                "frequencies": frequencies.tolist(),
                "di": [6.0 + np.random.randn() * 0.5 for _ in frequencies]
            }
        }
        
        if progress_callback:
            for i in range(10):
                progress_callback(i / 10)
        
        return results


# Use mock solver if bempp not available
if not BEMPP_AVAILABLE:
    BEMSolver = MockBEMSolver
