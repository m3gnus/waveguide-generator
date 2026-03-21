import logging

import numpy as np
from typing import Dict, List, Optional, Tuple

from .deps import BEMPP_AVAILABLE

logger = logging.getLogger(__name__)
from .device_interface import selected_device_metadata
from .mesh import prepare_mesh
from .solve import solve_optimized

_STABLE_ADVANCED_SETTINGS = {"use_burton_miller"}
_IGNORED_COMPAT_ADVANCED_SETTINGS = {"enable_warmup", "bem_precision"}


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

    def prepare_mesh(
        self,
        vertices: List[float],
        indices: List[int],
        surface_tags: List[int] = None,
        boundary_conditions: Dict = None,
        mesh_metadata: Dict = None,
    ) -> Dict:
        return prepare_mesh(
            vertices,
            indices,
            surface_tags,
            boundary_conditions,
            mesh_metadata,
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
        requested_device_mode = str(device_mode or "auto")
        effective_device_mode = "auto"
        if requested_device_mode != effective_device_mode:
            logger.info(
                "[BEM] Ignoring compatibility device_mode=%s; active /api/solve runtime uses auto selection.",
                requested_device_mode,
            )

        device_info = selected_device_metadata(effective_device_mode)
        selected = device_info.get("selected", "unknown")
        fallback_reason = device_info.get("fallback_reason")
        if fallback_reason:
            logger.info(
                "[BEM] Device interface: %s (requested=%s, effective=%s, selected_mode=%s, reason: %s)",
                selected,
                requested_device_mode,
                effective_device_mode,
                device_info.get("selected_mode"),
                fallback_reason,
            )
        else:
            logger.info(
                "[BEM] Device interface: %s (requested=%s, effective=%s, selected_mode=%s)",
                selected,
                requested_device_mode,
                effective_device_mode,
                device_info.get("selected_mode"),
            )

        if not use_optimized:
            logger.info(
                "[BEM] Ignoring compatibility flag use_optimized=%s; stable solver path is always active.",
                use_optimized,
            )

        provided_advanced_settings = dict(advanced_settings or {})
        runtime_advanced_settings = {
            key: value
            for key, value in provided_advanced_settings.items()
            if key in _STABLE_ADVANCED_SETTINGS
        }
        ignored_advanced_settings = sorted(
            key
            for key, value in provided_advanced_settings.items()
            if key in _IGNORED_COMPAT_ADVANCED_SETTINGS and value is not None
        )
        if ignored_advanced_settings:
            logger.info(
                "[BEM] Ignoring compatibility advanced_settings override(s): %s. "
                "Active /api/solve runtime exposes use_burton_miller only and keeps solver numerics fixed "
                "(single precision, no warm-up).",
                ", ".join(ignored_advanced_settings),
            )
        return solve_optimized(
            mesh, frequency_range, num_frequencies, sim_type,
            polar_config, progress_callback, stage_callback,
            verbose=verbose,
            mesh_validation_mode=mesh_validation_mode,
            frequency_spacing=frequency_spacing,
            device_mode=effective_device_mode,
            **runtime_advanced_settings,
            cancellation_callback=cancellation_callback,
        )
