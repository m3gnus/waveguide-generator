import logging

import numpy as np
from typing import Dict, List, Optional, Tuple

from .deps import BEMPP_AVAILABLE

logger = logging.getLogger(__name__)
from .device_interface import selected_device_metadata
from .mesh import refine_mesh_with_gmsh, prepare_mesh
from .solve_optimized import solve_optimized


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
        verbose: bool = False,
        mesh_validation_mode: str = "warn",
        frequency_spacing: str = "linear",
        device_mode: str = "auto",
        advanced_settings: Optional[Dict] = None,
        cancellation_callback: Optional[callable] = None,
    ) -> Dict:
        """
        Run BEM simulation through the stable solver runtime.

        Args:
            mesh: Mesh dictionary from prepare_mesh
            frequency_range: [start_freq, end_freq] in Hz
            num_frequencies: Number of frequency points
            sim_type: Simulation type
            polar_config: Polar directivity configuration
            progress_callback: Progress callback function
            use_optimized: Compatibility-only legacy flag. Ignored by runtime.
            verbose: Print detailed progress and validation reports (default: False)
            advanced_settings: Optional stable-runtime overrides exposed by the
                public contract.

        Returns:
            Results dictionary with simulation data and metadata
        """
        device_info = selected_device_metadata(device_mode)
        selected = device_info.get("selected", "unknown")
        fallback_reason = device_info.get("fallback_reason")
        if fallback_reason:
            logger.info(
                "[BEM] Device interface: %s (requested=%s, selected_mode=%s, reason: %s)",
                selected, device_mode, device_info.get("selected_mode"), fallback_reason,
            )
        else:
            logger.info(
                "[BEM] Device interface: %s (requested=%s, selected_mode=%s)",
                selected, device_mode, device_info.get("selected_mode"),
            )

        if not use_optimized:
            logger.info(
                "[BEM] Ignoring compatibility flag use_optimized=%s; stable solver path is always active.",
                use_optimized,
            )

        runtime_advanced_settings = dict(advanced_settings or {})
        return solve_optimized(
            mesh, frequency_range, num_frequencies, sim_type,
            polar_config, progress_callback, stage_callback,
            verbose=verbose,
            mesh_validation_mode=mesh_validation_mode,
            frequency_spacing=frequency_spacing,
            device_mode=device_mode,
            **runtime_advanced_settings,
            cancellation_callback=cancellation_callback,
        )
