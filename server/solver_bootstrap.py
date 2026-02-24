"""
Solver dependency bootstrap: conditional imports and availability flags.

All solver-availability flags and runtime functions are centralised here so that
route modules and service modules can import from a single stable location.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# ── BEM solver ────────────────────────────────────────────────────────────────
try:
    from solver import BEMSolver
    from solver.contract import normalize_mesh_validation_mode
    from solver.deps import (
        BEMPP_RUNTIME_READY,
        GMSH_OCC_RUNTIME_READY,
        get_dependency_status,
    )
    from solver.device_interface import normalize_device_mode

    SOLVER_AVAILABLE: bool = BEMPP_RUNTIME_READY
    if SOLVER_AVAILABLE:
        logger.info("[BEM] Device auto policy: opencl_gpu -> opencl_cpu -> numba")

except ImportError:
    SOLVER_AVAILABLE = False
    BEMPP_RUNTIME_READY = False
    GMSH_OCC_RUNTIME_READY = False
    BEMSolver = None  # type: ignore[assignment,misc]

    def normalize_mesh_validation_mode(value: Any) -> str:  # type: ignore[misc]
        mode = str(value or "warn").strip().lower()
        if mode not in {"strict", "warn", "off"}:
            raise ValueError("mesh_validation_mode must be one of: off, strict, warn.")
        return mode

    def normalize_device_mode(value: Any) -> str:  # type: ignore[misc]
        mode = str(value or "auto").strip().lower()
        if mode not in {"auto", "opencl_cpu", "opencl_gpu", "numba"}:
            raise ValueError("device_mode must be one of: auto, opencl_cpu, opencl_gpu, numba.")
        return mode

    def get_dependency_status() -> Dict[str, Any]:  # type: ignore[misc]
        return {
            "supportedMatrix": {},
            "runtime": {
                "python": {"version": None, "supported": False},
                "gmsh_python": {
                    "available": False,
                    "version": None,
                    "supported": False,
                    "ready": False,
                },
                "bempp": {
                    "available": False,
                    "variant": None,
                    "version": None,
                    "supported": False,
                    "ready": False,
                },
            },
        }

    logger.warning("BEM solver not available. Install bempp-cl to enable simulations.")


# ── Waveguide OCC builder ─────────────────────────────────────────────────────
try:
    from solver.waveguide_builder import build_waveguide_mesh

    WAVEGUIDE_BUILDER_AVAILABLE: bool = True
except ImportError:
    build_waveguide_mesh = None  # type: ignore[assignment]
    WAVEGUIDE_BUILDER_AVAILABLE = False


# ── Legacy .geo mesher ────────────────────────────────────────────────────────
try:
    from solver.gmsh_utils import gmsh_mesher_available
    from solver.gmsh_geo_mesher import generate_msh_from_geo
except ImportError:
    def gmsh_mesher_available() -> bool:  # type: ignore[misc]
        return False

    def generate_msh_from_geo(*_args: Any, **_kwargs: Any) -> Any:  # type: ignore[misc]
        raise RuntimeError("Legacy .geo mesher backend is unavailable.")
