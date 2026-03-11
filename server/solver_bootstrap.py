"""Runtime dependency status flags for backend solver integrations."""

import logging

logger = logging.getLogger(__name__)

from solver.deps import BEMPP_RUNTIME_READY, GMSH_OCC_RUNTIME_READY, get_dependency_status

SOLVER_AVAILABLE: bool = BEMPP_RUNTIME_READY
if SOLVER_AVAILABLE:
    logger.info("[BEM] Device auto policy: opencl_gpu -> opencl_cpu")
else:
    logger.warning("BEM solver not available. Install bempp-cl to enable simulations.")

try:
    from solver.waveguide_builder import build_waveguide_mesh as _waveguide_mesh_builder
except ImportError:
    WAVEGUIDE_BUILDER_AVAILABLE = False
else:
    WAVEGUIDE_BUILDER_AVAILABLE = True
